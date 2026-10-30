.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Media Studio
============

The editor picker has a separate **AI** column: **Coding Agent**, followed by
**Media Studio** (named Generative AI before 1.0.5). Python Console remains in Scripting. All three editors have
separate input state. The compiled fork exposes both AI editor subtypes natively;
the installed stock Blender can preview the same GPU canvas via the host addon.

Each mode has one home for Media Studio (``studio_workspaces``, the binding entry-point spec is
``studio_entry_points.rst``): in Manual its own workspace tab, *Media Studio*, after Director and before
*Generation Flows*, one editor filling the area with no stock header, sidebar or toolbar; in Director its
own window. A file gets the tab once; one the person deleted is never added back to that file, and
+ Add Workspace ▸ Angelo brings it back (a C++ hook in ``workspace_edit.cc`` draws
``ANGELO_MT_workspace_add``). In Manual the top-bar Studio switches to the tab, pressed while it shows, and
a click then goes back ("Back to {workspace}"); every entry routes through ``open_media_studio``.
The tabs are built by switching the window through them (Blender's workspace operators act on the active
workspace), one event-loop pass per step, never queueing a switch to the workspace already shown: Blender's
``workspace.duplicate`` stores its copy's layout for the next switch, and a stale one took it for the wrong
workspace and crashed at startup. ``workspace_edit.cc`` now gives that stored layout only to the workspace that
owns it (``test_blender_workspace_stored_layout``). When the build runs: the session's first build is part of the
start-up (``startup_build_pending``): Angelo's default layout runs it before Director's (``startup_build``), under
Angelo's start-up cover, so the window never leaves Director's chat once it shows (1.0.5 RC: built after Director
had settled, it switched the window ~2.5 s in, under anything that had just found the chat), and the start-up cards
(setup card, start panel, welcome) wait for it, so a person never sees it or clicks during it;
a later build (a file loaded without the tabs) waits until no card, welcome or tool has held the layout for a second
(``startup_quiet``). A build holds the layout lock (``ui._layout_timer``) while it switches (``studio_tab_build``),
and a load that replaces the file or a quit during a build ends it and releases the lock (left held, the layout never
settled again) before Blender frees anything, with nothing rolled back into or read from the file going away
(``timer_jobs``: left to Python's finalization, its cleanup read freed data and crashed the 1.0.6 RC at quit); a load
that fails keeps the file, and the build goes on; a load that keeps the layout (Load UI off, as Restore opens a
checkpoint) and unregistering the add-on end it, and + Add Workspace's build, as any stopped build, rolled back,
since the workspaces stay. A tab carries ``angelo_tab_unfinished`` until its build
finishes it: quit.blend is written from the data after the quit has ended the build (a rollback's operators would apply
too late), and a file saved mid-build has it too, so the next build deletes a marked tab, waits for the delete to
apply, and builds it again, never adopting it (a marked tab is work for ``ensure`` even when no tab is missing, and
never shows as the pressed tab); the quit gives each workspace the build passed its own object mode back
first, as an ID write applies at once. The GUI
pump holds agent, connected-app and script-file tools while a build runs, at most two seconds, never dropping or
reordering them (a script changing an area the build then closed crashed the app); every workspace the build switches
to gets the window's object mode for the build and its own back after, since Blender applies a workspace's mode on
each switch (a sculpting object was left in Object Mode); one that stops
part-way deletes the tab it half-made, puts the window back where it was and is tried again at the next quiet
moment (1.0.5 first launch: a build that ran under the setup card lost the card, the welcome and the chat; one that
ran while a person clicked left UI blocks unfreed at quit). The strip reads Director · Media Studio · Generation Flows ·
Layout, placed with ``WorkSpace.angelo_move_after`` (no switch to Director, which would change mode) when the tabs
or Director's workspace are made, and restored on load. The tabs' editors, and a flow's own window, keep no stock header, sidebar or toolbar by construction
(``chrome_guard``): hidden when made and on load, then kept by ``bpy.msgbus`` subscriptions on the editor's own
flags, so N or T never brings them back; nothing polls.

Since 1.0.5 Media Studio is reachable without the editor picker, which Director
never shows (``studio_toggle.py``): first from the chat bar's **+** menu, Angelo's
main navigation (``agent_chat.rst``, "The + menu"), then **Studio** beside **Chat**
in the top bar of both modes (with **Flows** after it, ``generation_flows.rst``; each as
wide as its label in the current language, ``top_bar_fit``; icon-only, with Chat's
shortcut dropped, when the menus, workspace tabs and these buttons would not fit the
window, as at UI scale 2: Chat's shortcut and Manual's View Layer selector give way first, and Studio
and Flows are never removed), the Window menu (Media Studio and the Generation Flows submenu, beside
Angelo Chat), every Asset Browser's header (Media Studio and Generation Flows pills, ``asset_browser_entry``;
they stay in every state of the list and sit in the free span just left of Blender's search group, right of Import
Settings when it shows, so a pill never covers the search field or a button: Blender's menus are measured in the
interface language, and the pills are labelled when the labelled pills fit that span, icon only when the icons fit,
none when Blender's own items already fill it), an Asset Browser's empty and no-results states (below), and
**Media Studio** at the top of the Angelo menu in both modes. Each opens
Media Studio in its own window: the largest editor is copied into a new window
(Blender's Duplicate Area into New Window, as the chat's Pop Out does) and the copy
becomes the Media Studio editor, so Director's split and a Manual layout are
never rearranged. The editor is changed with its window in the context
(``editor_types.set_editor``): Blender sets up a new editor's regions through the
context's window, and the welcome card opens Media Studio from a timer, where
there is none (1.0.5's first cut drew that window blank). That window shows Media
Studio alone, its own Image / 3D tabs at the top: Blender's editor header (the
editor-type dropdown, "Media Studio", View) is hidden there; a Media Studio the
person places in a layout keeps it, so its editor can still be changed. The button is pressed while that window is open and a click
closes it; a Media Studio the person placed in a layout is never closed from there.
The welcome screen's slideshow (below) opens it too, from its Media Studio slide, and the empty chat suggests
asking for a reference image or an image-to-3D model (``first_run.GENERATION_EXAMPLES``).

Every Asset Shelf has two pills (``asset_shelf_add.py``; ``studio_entry_points.rst``
revision 3, never "Add asset", which in Blender means Mark as Asset): **Media Studio**
(its spark-frame icon) then **Generation Flows** (the three-node glyph), 6 px apart.
Each is a ``PANEL`` pill with a ``LINE`` border, ``PANEL_HOVER`` under the pointer and
``TEAL_SOFT`` while pressed, never teal-filled. Both drop to icons only before a label
would be cut. They sit on the shelf's top edge, at its left end, clear of the toolbar,
the Adjust Last Operation panel and Angelo in the corner. They follow the shelf regions
of the 3D view and the Image editor, never a list of shelf classes, so every brush
shelf, Object mode's shelf and any later shelf has them. Media Studio always opens on
the shelf's tab: Image for texture paint, the Image editor and Object mode's Images
catalog tab, 3D otherwise. Generation Flows calls ``angelo.show_flows``: Manual mode's
Generation Flows tab, or Director's Flows popup. The pills are drawn over the view and not in the shelf's header,
because Blender draws only the first header type of a region
(``ED_region_header_layout``), which is the shelf's own C++ one. Their input is a
passive canvas input (``input_policy.CANVAS_INPUTS``): presses on a pill only, so a
brush stroke is never delayed.

Object mode has its own Asset Shelf (``media_shelf.py``): stock Blender's 3D-view
shelves hold brushes only, so a generated model or material could appear in none of
them. It lists Object, Collection and Material assets from every library, including
*Angelo Media Studio*. A catalog added as a tab in its catalog popover stays for every
file (``STORE_ENABLED_CATALOGS_IN_PREFERENCES``). Blender's own drop boxes handle a
drag from it: a model is added where it is dropped, and a material dropped on an
object is assigned. It starts collapsed to its tab strip, so the viewport is not
crowded (``DEFAULT_COLLAPSED``). The round chevron first among its pills expands it
to one row of small previews and collapses it again, and picking a tab of a
collapsed shelf expands it, as does "Show my assets". Hidden, collapsed or expanded
is remembered per user (``asset-shelf.json``, ``object_shelf_state``; an older
true/false choice reads as expanded/hidden) and applied to every Object-mode 3D
view. A change counts only after a view has been in Object mode for ``SETTLE``
seconds, and never in Director, which hides every shelf.

Collapsing is native. ``RGN_FLAG_COLLAPSED`` marks a hidden region whose regions
that hide with it (``RGN_ALIGN_HIDE_WITH_PREV``, the shelf's tab strip) stay
visible (``area.cc``). The collapsed region gets no unhide tab of its own, and the
flag clears whenever the region shows again. Each shelf instance keeps its own
state (``ASSETSHELF_REGION_IS_COLLAPSED``), so a collapsed Object shelf leaves
Sculpt's brush shelf as it was. Python reads ``AssetShelf.angelo_collapsed`` and
calls ``angelo_set_collapsed(collapsed)``; a shelf type opts in with
``bl_options`` ``DEFAULT_COLLAPSED``. The *Angelo Media Studio* library's two catalogs, Models and
Images (``studio_assets.CATALOGS``: fixed ids, names in the interface language), are
added as tabs once per user (``asset-shelf.json``, ``tab_offered``). A person who
removes them keeps them removed, and a catalog renamed into another language moves
its kept tab. Until the library has its catalogs (Part E's first sync), the offer
waits and retries. ``media_shelf.show_studio_tab(window)`` opens the shelf on that
library's All tab: the welcome's "Show my assets". Python reaches the tabs through Angelo's RNA additions
to ``AssetShelf``: ``enable_catalog(path)``, ``disable_catalog(path)``,
``is_catalog_enabled(path)`` and ``active_catalog_path``, where an empty path is the
All tab (``rna_ui.cc``, over the existing shelf settings).
Collapsing the shelf to its tab strip waits for 1.0.6: the header region hides with
the shelf (``RGN_ALIGN_HIDE_WITH_PREV``), and changing that is a layout change.
Director hides every shelf, as it shows only the 3D view and the chat.

A shelf that lists nothing says so (``shelf_empty.py``): when its library has finished
loading and its last build listed no asset (Angelo's read-only
``AssetShelf.angelo_shown_count`` and ``angelo_list_ready``, recorded where the shelf
builds its items), one centred line shows Media Studio's glyph, "Your generated images
and models appear here" and a teal **Generate in Media Studio**, which opens Media
Studio on the shelf's tab. The message is dropped before the button when the shelf is
narrow. Only Angelo Media Studio's own view says this (``shows_studio``): Angelo's
Object-mode shelf on the Angelo Media Studio tab or a catalog under it, from every
library or that library, or that library's All tab. Any other empty shelf (a brush
shelf, a person's own catalog, Current File, another library) shows no Angelo state
and keeps Blender's own, since generating in Media Studio would not fill it. The pills
stay in every state, and they show in every interactive run, automation included, so
suites see the product as people do.

Every surface opens Media Studio on a tab through one opener,
``studio_toggle.open_media_studio(kind)``. A ``kind`` of "3d" or "image" selects that
tab; None keeps the current one. It routes by mode: in Manual it switches to the
*Media Studio* workspace tab (``studio_workspaces.show``) and selects the tab once
the switch lands; in Director, or when that tab is gone, it opens Media Studio's own
window. ``angelo.show_flows`` routes Generation Flows the same way. The
``angelo.open_media_studio`` operator (F3: *Media Studio*) takes ``kind`` AUTO, 3D or
IMAGE. Contextual entries (the shelf's and the Asset Browser's pills) pass 3D or IMAGE
and always select it. General entries (menus) pass AUTO. Until Studio first shows in a
session, AUTO takes the tab from the context the person came from; after that it keeps
the current tab. ``studio_kind(context)`` picks the tab from where the person is
working. A surface's look gives Image: texture paint, the Image editor, a shader or
texture node tree, the Material or Texture properties, or a material, image or texture
asset. Everything else gives 3D: objects, meshes, collections, sculpt, and a brush
asset outside texture paint. Blender cannot raise a
window, so a Studio window the opener made before is closed and opened again in
front, keeping its prompt, references and folder. A Media Studio in the person's own
layout is left alone.

Media Studio's window wears the shared tokens (``studio_entry_points.rst`` §4; the
maintainer's decision of 10-20). Its canvas is ``CARD`` with ``PANEL`` columns and
fields. The accent fill (primary button, active tab, progress) is ``TEAL`` with
``INK`` text, ``TEAL_HOVER`` on hover. A focused field is outlined in ``FOCUS``, a
chosen chip or folder is ``TEAL_SOFT``, and success and failure are ``SUCCESS`` and
``DANGER``. ``studio*.py`` holds no literal colour (pinned by a host test), and
Angelo-drawn canvases keep these tokens in the Light theme too. The editor is split
along its responsibilities: ``studio.py`` (state, the frame, clicks, registration),
``studio_draw.py`` (controls and panels), ``studio_input.py`` (the canvas input),
``studio_ops.py`` and ``studio_asset_ops.py`` (operators). Every name stays reachable
as ``studio.<name>``. The right end of the top row is one hook, ``studio.top_row_end``.
It draws "Turn into a flow" (``studio_to_flow.draw_pill``, action ``to_flow``) when
that module exists, else the "Local media library" label. Top-row actions
(``TOP_ROW_ACTIONS``) are never clipped to the Create column. The primary button is
``TEAL``, ``TEAL_HOVER`` under the pointer and ``TEAL_PRESSED`` while held.

The Studio uses Blender GPU/BLF and native operators, not an embedded browser,
MCP or screenshots-as-controls. Chat orchestrates the same direct API workers;
the coding model does not generate the images or meshes itself. It has Image, 3D
and Workflows tabs (see "Workflows"). Create, Folders, and the asset grid occupy non-overlapping columns.
It draws with Angelo's shared tokens (``ui_tokens``/``card_kit``): teal primary
actions with ink text, the shared secondary fill, ``ui_kit`` field colours.
**Hide Create** returns the left column to the library; **Create** opens it again.
On narrow editors Create and Library alternate rather than covering another
Blender editor. Create settings scroll independently and Generate stays visible.

Models are a compact single-click dropdown. Each model retains its exact named
API connection. Documented but unconnected models are discoverable setup choices,
not advertised entitlements. Removing a selected account does not switch billing
to another account. Connections and keys belong in Preferences > AI Providers.
Under the model, the Create panel names its account, or the one that is missing
(*No OpenAI account yet*, ``studio_state.account_line``), above its one
**Connect in AI Providers** button.

References support selected PNG/JPEG/WebP files, native file drop, and public
HTTPS URLs. References are private local snapshots. URL references are fetched
only when Generate is confirmed, with no provider authentication headers. The 3D
flow takes one reference image or a text prompt; multi-view, painting,
materials, animation, video, and audio generation are not supported.

Director Mode and chat operation
--------------------------------

Normal native Angelo GUI startup defaults to **Director Mode**: the 3D view and
Coding Agent chat, with Properties, Outliner, Timeline, workspace tabs, viewport
headers, toolbars, sidebars, gizmos and reference overlays (floor grid, axes,
text, 3D cursor) hidden. Selection outlines, cameras and lights stay visible.
The **Director / Manual** menu is in the **top-right beside Scene**, because it
controls workspace presentation, not an individual chat; the **Chat** button
left of it is the same in both modes (``agent_chat.rst``, "Show and hide").

Director keeps Blender's own top-bar menus (Angelo, File, Edit, Render, Window,
Help: ``TOPBAR_MT_editor_menus``, collapsed to one menu when the bar is narrow),
so Save, Open, Undo, Import/Export, Preferences and Render are where they are in
Manual mode and Blender, each with its shortcut. Only the workspace tabs are
hidden. Before 1.0.4 Director replaced the menus with a title: a user could save
only by switching to Manual, since the shortcuts were named nowhere ("The menu bar
disappears in director mode", 2026-10-21), and Help items had been re-added to the
mode menu one at a time. Window > Director Mode reads Manual Mode in Director (the
toggle, so its shortcut shows). ``test_blender_mode_basics`` checks the menus are
drawn in both modes and that the platform's Save and Undo keys (read from the user
keymap) work in both, with the pointer over the chat and over the 3D view. Scene remains a scope
selector in Director mode, without New/Delete or view-layer editing controls.
Switching scenes retains the existing document-bound chat guard; start a new
chat before issuing mutations against a different scene.

Director hides the status bar, so the top bar carries what it would show, after
the menus (``director.draw_director_status``): the report banner
(every operator error, for example a refused Accept), Blender's running-job
progress, the agent's background-job item with an icon Cancel/Open, and a pending
update. ``test_blender_director_reports.py`` raises a real operator error through
the event system and checks the banner's pixels and the job item, in English and
Arabic.

Director is a dedicated duplicate workspace. Only that copy's areas are closed;
manual editors and their cached settings are not destructively joined. Manual
mode restores the previous workspace and the latest unsent chat draft. Returning
to Director restores its preview and current draft. The non-secret mode preference
is saved in ``workspace-mode.json`` under the host's configuration directory;
first use defaults to Director, subsequent launches remember Manual when chosen.
Angelo starts in Director. Blender's splash shows its art and version once,
never a Blender form: Blender builds it before its main loop, while
``angelo_agent`` is enabled later by the default layout, so the startup module
``angelo_default_layout`` puts stand-ins in place of Blender's two splash menus in
its first ``load_post`` (after every startup script, before the splash opens).
While Angelo's startup is pending they draw nothing, and ``splash_setup.py``
takes the menus over when the add-on registers. Blender's Quick Setup form and
New File page never show in Angelo, in any launch mode: when Angelo's startup
will not run (``ANGELO_DISABLE_DEFAULT_CHAT``, ``--factory-startup`` with an
empty profile, a start that ended without the add-on, the add-on turned off,
Reload Scripts) the splash keeps its art alone, because every class that can own
the two menus, Blender's own in ``bl_operators/wm.py`` included, draws nothing
there. The splash itself is C (``wm_splash_screen.cc``) and draws only the menu
registered under each name. Director's startup switch closes the splash (setting a workspace
closes every popup) and it is never reopened. Under Blender's startup rule,
Angelo's own card then follows, drawn with ``card_kit`` and hosted modally by
``card_host``:

* **First launch** (no saved preferences): the setup card (``setup_card.py``).
  Language is a list of Angelo's 21 languages in their own names; Look is
  "Angelo Dark" / "Angelo Light", the kit's segmented control with a swatch each
  and one selection mark (the bundled theme presets ``Angelo_Dark.xml`` and
  ``Angelo_Light.xml``, named the same in Preferences > Themes; the card applies
  Angelo's bundled file, never a same-named one among the person's presets);
  "Use my Blender settings" brings the person's earlier settings
  (``settings_import``): an earlier Angelo version's folder through Blender's own
  import (``preferences.copy_prev``; the row then reads "Use my earlier Angelo
  settings"), otherwise stock Blender's own user folder, newest version up to
  Angelo's (5.2 first). Its note names the app and version ("from Blender 5.2",
  "from Angelo 5.1"); it is disabled with a reason when there is nothing to bring or
  a ``BLENDER_USER_*`` folder is already shared. Mouse and Spacebar choices sit
  behind a disclosure. Choices apply at once; Continue saves the preferences (or
  imports) and Escape skips without saving, as Quick Setup did. The import keeps
  the card's language and look (and the mouse and Spacebar choices changed there)
  and Angelo's own add-on, then the welcome follows; after it, a corner notice says
  what came over and names, in one line, any add-on that did not load. What is
  copied, what is left out (Blender's start-up file among it) and the later import
  from File > Defaults with its undo: ``app_identity.rst``, "Bringing Blender
  settings over". ``test_blender_settings_import`` runs both with a sandboxed stock
  Blender folder.
* **Returning user**: the start panel (``start_card.py``) instead of Blender's
  New File page: recent scenes from ``recent-files.txt`` as thumbnail tiles (the
  preview embedded in each file; a drawn placeholder only when the file has none)
  with the title of the chat that was active when the scene was last saved
  (``scene-chats.json`` beside the conversations index, written on save) or else
  when it was last edited ("Edited 3 days ago", plural-aware), New scene, Open…
  and What's new. No templates and no Donate (About has it). Blender's splash
  opened later (Help > Splash Screen) gives way to the same panel, or to the setup
  card while no preferences are saved.

Custom controls the kit does not have yet (dropdown, row panel, scene tile) are in
``firstlaunch_controls.py``; the segmented controls and the switch are the kit's. The
sequence is splash, setup, welcome, each once (``splash_setup.sequence`` records it
for fixtures; the stand-ins' draws count as the splash). A .blend opened at first
launch (the Finder's open-document event or the command line) still gets the setup
card, then the welcome: a file load drops ``intro``'s scheduling timer, and closes a
visible setup card unanswered, so ``intro.file_loaded`` reports the startup card as
still owed and the default layout decides it again for the opened file. The
not-connected showcase (``connect_hero``) yields to the cards as it does to the
welcome, whichever opened first: a card paints it beneath itself and the showcase's
input handler passes on what the card takes. A
welcome screen (``intro.py``, drawn by
``welcome_screen.py`` over the 3D view) then follows on first launch, once the
setup card has closed and Director has settled: a banner, what Director and Manual are
(the second mode has one name everywhere since 1.0.4; the welcome said "Standard
editor" before), that Director keeps Blender's menus,
how to switch (the Director / Manual menu and the shortcut), a quick tour of features
(*What you can do*), demo tiles (``first_run.DEMOS``) and Start creating, plus Try Manual mode.

The tour (``welcome_slides``, 1.0.5) is a slideshow in the order a new person needs it, its copy fixed by
``studio_entry_points.rst`` ("First run"): *Describe it, Angelo builds it* (the chat), *Start in Media Studio*,
*Go further with Generation Flows* and *Everything you make, ready to use*. Each slide has a vector picture in the
card's colours (``welcome_art``: no image files, sharp at any scale, drawn upright in a right-to-left language like
the layout diagrams; Media Studio's and Generation Flows' pictures carry their concept icons, drawn from the
buttons' own pixels), a headline, one line and a secondary pill whose verb says where it goes, with a tooltip;
Start creating stays the welcome's one primary button. A pill closes the welcome and, once its handler has ended,
opens the feature through the opener every other entry uses: *Try an example* shows the chat as its top-bar
button does and, once an account is connected, puts the first example in the composer, unsent and focused
(``angelo.chat_example``); *Open Media Studio* (``angelo.open_media_studio``, AUTO) and *Open Generation Flows*
(``angelo.show_flows``; in Director the popup anchored under the top bar's Flows button,
``flow_menu.open_from_top_bar``) go to the mode's home, a tab in Manual and a window or popup in Director; *Show my assets*
shows the 3D view's Asset Shelf on its Angelo Media Studio library (``media_shelf.show_studio_tab``), the one pill
that changes a layout, which its slide names; Director shows no shelf, so there it first switches to Manual. That slide exists only where the shelf can do it (``SHELF_OPENER``), so the tour never promises a
feature the build lacks. No pill sends a message or needs an account. The slideshow opens on its first slide and
advances every ``DWELL`` seconds (``auto_next``): it holds while the pointer is over it or a control in it has
keyboard focus, stops for good once the person navigates, comes to rest on the last slide rather than looping, and
never moves under Reduced Motion, which also drops the fade. The arrows, the dots, Left/Right and a sideways
trackpad swipe change slides (mirrored in Arabic, where Left goes to the next slide). Every slide, and every pill, is laid out at the
size of the largest in the current language, so changing slides moves nothing; on a 3D view too short for the
whole welcome even at its smallest scale, the slideshow drops its picture and puts its pill and arrows on the
headline's row (``compact``). ``test_native_welcome_slides`` and ``test_blender_welcome_slides`` pin it. It marks the mode the
window is in (*You're here*), so opened from Help in Manual mode it marks Manual and
offers no Try Manual mode. The chat beside it stays usable. Over the 3D view it takes the person's
pointer and keys, so nothing behind it reacts, and passes on what is not input
(timers, window events), Blender's motionless hover refresh and the quit key:
Blender's quit waits for that refresh, so a welcome opened just after a quit
request (as a timer can open it) never holds the quit. Every Angelo modal handler
follows the same rule (``input_policy.overlay_passes``; the host test
``test_native_modal_pass_through`` names each one). It opens again from Help and from the
Director / Manual menu, and never by itself after it has been closed or when an
older build already saved a mode. ``ANGELO_DISABLE_INTRO=1`` turns the startup
cards and the welcome off for automation (the splash still shows only its art); the
native suite runner sets it for every suite except those marked ``first_launch``
(``test_blender_first_run``), which see the real first frames;
``test_blender_start_panel`` and ``test_blender_translation`` turn it back on
themselves. ``test_blender_first_launch_frames`` checks every frame of warm and cold
first launches, the second launch, Help > Splash Screen, a .blend opened from the
Finder or the command line, Preferences > Themes, the Look control in English and
Arabic, automation's opt-out (``ANGELO_DISABLE_DEFAULT_CHAT`` with
``--factory-startup`` and an empty profile), the add-on turned off and a failed
start for Blender's form (which classes drew the splash's menus, and a form's
selected radio button in the splash's band), with a control run whose fixture
menu draws such a row and must be found. ``test_native_startup_splash`` draws every
class that can own the two menus, Blender's own included, in each of those states
and fails on any layout call. The banner art is generated by
``release/branding/angelo/welcome_banner.py`` from the raven master.
``Ctrl+Alt+M`` (Control+Option+M on macOS) switches between Director and Manual from
any editor (``shortcuts.DIRECTOR``, in the add-on keyconfig's Window keymap). It is the
shortcut every menu, tooltip, hint and doc shows: the Director / Manual menu's item for
the other mode is the toggle itself, and the welcome screen names it as bound, so a
rebinding shows. 1.0 to 1.0.2 used ``Ctrl+Alt+D``, which Blender binds in the "User
Interface" keymap (``anim.driver_button_remove``, consulted on every header, sidebar,
Properties, Outliner, timeline, node, text and file region before the Window keymap
and consuming the key even with no driver under the pointer) and in the Outliner
(``outliner.drivers_delete_selected``), so it worked only over the 3D view; winning
there would have taken Blender's remove-driver shortcut from every field. Ctrl+Alt+D
still switches over the 3D view's main region, where nothing else takes it
(``shortcuts.DIRECTOR_VIEWPORT``: the "3D View" keymap, its own internal operator so
nothing names it). In the legacy keymap that hides the Window debug menu's Ctrl+Alt+D
over the 3D view (F3 still finds it). Every window-wide Angelo shortcut is free in
every non-modal keymap of Blender's default, left-click, legacy and
industry-compatible keymaps, and the viewport one takes no chord from a keymap that
acts there (``test_native_shortcuts``); ``test_blender_director_shortcut`` presses
Ctrl+Alt+M over each editor and checks Ctrl+Alt+D switches over the 3D view, still
removes a driver from a field and deletes drivers in the Outliner. Layout changes
are deferred and wait for foreground native modal tools. Director adds no input
handler: every viewport navigation (middle mouse, wheel, trackpad, numpad, Home,
frame selected, the view pie, NDOF), selection, transform and menu shortcut
reaches Blender natively. The chat keeps keyboard focus like a text field, so
typed letters stay in a focused chat even with the pointer over the 3D view,
while view-navigation keys always follow the pointer; Escape (the draft stays),
orbiting, scrolling or clicking elsewhere moves focus there (``input_policy.py``).
Background jobs skip default layout creation; ``ANGELO_DISABLE_DEFAULT_CHAT=1``
remains the explicit automation opt-out. Disabling the host restores manual UI.

Multi-step work (a sheet, then a turnaround, then a model) uses Generation Flows,
which compose these same models and workers as a node tree with one itemized
approval per run; see ``generation_flows.rst``. While signed in to an Angelo
team, the team library and IP tools can import a team image into this library
(a generation reference) and save Studio outputs to the team library with their
provenance; see ``team_library.rst``.

No Media Studio editor needs to be opened for its supported operations. Both
Codex dynamic tools and Claude typed plans receive these native host tools:

* ``angelo_studio_catalog``: exact named generation accounts/models, availability,
  capability-scoped controls and enhancement account choices. Never credentials.
* ``angelo_studio_list`` / ``angelo_studio_asset``: folders, bounded search,
  pagination, durable job status, provider task IDs and optional preview pixels.
* ``angelo_studio_generate``: image/text-to-3D/image-to-3D using one exact API
  account, optional supported controls and attached/library/public-HTTPS image
  references. UUIDs are conversation-scoped or admitted library image IDs, never
  arbitrary local file paths. A library image can be reused as a reference.
* ``angelo_studio_enhance``: the configured enhancer, or an explicit account/model
  override. Returns the original and improved suggestion without automatic Apply.
  Apply/Discard/Undo are chat decisions using those original/suggested prompts.
* ``angelo_studio_create_folder`` / ``angelo_studio_move_asset``: organization.
* ``angelo_studio_import_image``: an image attached to the current conversation.
* ``angelo_studio_import_3d``: separately approved, self-contained native GLB
  import into the bound scene, with actual object read-back and Blender Undo.
* ``angelo_studio_export``: a new relative output path in the selected chat file
  workspace, with exclusive creation, matching extension and no overwrite/escape.
  Exports up to 150 MiB are journaled before creation and restored with request
  snapshots, not Blender Undo. A protected safety checkpoint can preserve an
  existing journaled export before Restore, so Undo Restore can recover it.
  Ordinary file-tool reads/writes/backups keep their separate 20 MiB limit.
  Unsupported hard-link filesystems refuse safely rather than risk overwriting.
* ``angelo_studio_stop``: stop local monitoring, not guaranteed provider cancellation.
* ``angelo_set_workspace_mode``: request Director/Manual presentation through chat.

Generate and Enhance **always require individual usage approval**, including in
Auto bounded mode. Approval shows the exact named account/model, prompt, options,
reference upload count, separate API billing and two-stage Meshy disclosure when
applicable. Removed accounts, rotated keys or changed references invalidate the
pending approval; there is no billing fallback. Read-only chats cannot submit
usage or library changes. Provider work and enhancement run off the UI thread.
The controller defers the tool response until a terminal result, with progress
shown in chat and previews delivered to image-capable agents and the transcript.
Large images get a private 768-pixel PNG preview while the full-size library
output stays untouched. In-flight monitoring consumes its worker event stream
rather than rescanning the entire library on every UI tick.
Stop/disconnect cancels local monitoring; durable assets/task receipts survive.
It never silently retries a paid request or auto-imports a generated model.

The agent is instructed to discover capabilities, ask which account to bill when
ambiguous, use these tools instead of sending users to Studio/Properties, and
not invent video/audio/material/animation generation. Keys and sign-in are never
typed into a chat message: they stay in **AI Providers** (reachable from the chat
header's settings button) and the chat's Connect card, which opens the provider's
own sign-in or the connection form.
Library organization, API billing and manual exports are external effects, not
reverted by Blender Undo or scene-snapshot restore. Agent workspace exports are
journaled and restored with snapshots, but are not part of Blender Undo.
Undo/file-load invalidated preview RNA handles are discarded and reloaded from
the durable library, including during addon shutdown.

Workflows
---------

The **Workflows** tab (``studio_workflows``, ``studio_workflow_ops``) runs a bundled
Generation Flow template marked ``studio`` (``generation_flows.rst``, "Templates and
character parts") as plain input → output. It is Media Studio's third tab (Image, 3D,
Workflows), and the chat bar's **+** ▸ **Workflows** opens Studio on it
(``open_media_studio("workflow")``, the WORKFLOW kind of ``angelo.open_media_studio``:
Studio's window in Director, its workspace tab in Manual; ``studio_entry_points.rst``).
The tab's run is never replaced from there: the template is chosen on the card.
Its columns are the template card, this file's **Runs**
(flow trees tagged ``angelo_template``) and a board. The card holds only the
template's input (a character image, imported into the library, or a description),
the height, the account each paid step resolves to, and the next action. Every
run is an ordinary flow tree, so ``angelo.flow_run``'s itemized dialog approves
it, and **Open in Generation Flow** shows it in a Node Editor beside the Studio, or
in the flow window (``flow_open.show``) when there is none.

Media Studio is drawn with Angelo's shared tokens (``card_kit`` / ``ui_tokens``):
charcoal surfaces, teal for primary actions and the selected tab with ink text, the
shared secondary fill for other buttons, the ``ui_kit`` field fill and focus colour,
teal for done and coral for failed. The template is presented as a starting point to
finish in Blender; nothing in the product is showcased with renders of it.

``studio_workflow_view`` (pure) reads the run's stage from the flow states that
``flow_host`` prepared: FILL, SHEET, REVIEW (the sheet is split and no part is
modelled), PARTS, PLACE (the body's model is ready and no part is still being
made). What a click does is ``studio_workflow_run``'s. At the review stop the
board outlines each part on the sheet from the split's stored grid, and parts are
named by kind (translated; slot ids and object names stay English). Parts turned
off on the card start unticked in the run dialog (``angelo.flow_run``
``exclude``) and are kept on the run's flow (``angelo_template_skip``); the body
has no switch, because Place Character needs it. **Retry failed parts** starts
only the failed part steps ticked, at PARTS and at PLACE. **Redo sheet** asks
for a new take of the sheet's unchanged request (``angelo.flow_run`` ``retake``):
the take is advanced only when the run is approved, so declining changes nothing.
A description run shows its generated character image on the board before the
sheet is made from it. A run's inputs are written into its flow only while it is
at FILL, and only those the person changed on the card since the card and the run
last agreed (an edit made in the Node Editor, or a URL source, is taken back onto
the card, and shows there as soon as the flow is re-read); while the sheet is
being made they are shown read-only. A run opened
from **Runs** shows its own inputs, read back from the flow. Creating the run's
flow and Place in scene are undo steps (an operator called from Python pushes
none unless asked), so Undo after Place removes the character and keeps the run.
The description is a paragraph field like Studio's prompt
(``studio_state.MULTILINE_FIELDS``). **Place in scene** runs
``angelo.flow_place_character`` through a window that shows the scene
(``studio_host.scene_view``), and the card lists hats, hair and accessories to
check, presenting the result as a starting point.

**The parts check** (``parts_check``, ``studio_enhance.check_native``) compares
the sheet with the character image on the person's AI Enhance account (Claude or
Codex only) after a dialog naming the account and what is sent. It only reports.
Both images are sent as reduced private copies (``studio_host.write_fitted``,
1568 px; a generated sheet is over the 5 MiB attachment cap at full size), removed
after the check. It is not offered (``parts_check.READY``) until
``parts_check.MEASUREMENT`` names its accuracy record on the ten research
characters, as the maintainer decided; ``tests/python/angelo_agent/measure_parts_check.py``
measures it through the shipped path.
The user guide describes it when it ships.

**Fit parts to my image** (``parts_ground``, ``studio_enhance.ground_native``,
``angelo.studio_workflow_fit``) asks the same AI Enhance account, after the same
kind of dialog, where each part sits on the character: it sends the character
image (1024 px) and one image of the split's cut-outs on white, numbered with the
body as 0, and gets back a box per part in 0-1000 coordinates of the character
image. The result is stored with the run and split it was made for
(``flow_split_host.store_grounding``, keyed by the split key; a new split drops
it, with a notice), whatever the Studio area shows when it arrives, and as an
undo step of its own, so Undo after Place keeps it. Place Character then puts
each found part where the image shows it; a part the reply misses keeps its
sheet box, and a reply without a usable body box, or with a figure that runs off
the image, changes nothing. Parts turned off on the card are not sent. A request
that takes over 180 s ends with a notice. A busy or throttled provider (``notice_kinds`` "overloaded" or "rate") is asked again after 10, 30 and 60 s
(``studio_enhance.with_capacity_retries``, for every one-shot AI Enhance request: enhance, the parts check and the
fit); a Stop ends the wait, and a provider still busy after that shows the chat's "provider is overloaded" notice. Storing a fit changes no step's input key, so nothing is paid for
again. It is offered at the review stop and at the starting point, and only for
a runtime in ``parts_ground.MEASURED``: the shipped path is measured per runtime
with ``tests/python/angelo_agent/measure_parts_ground.py`` against the
pre-registered rule (research folder ``compose-eval``). Once stored, the card
says how many parts were fitted.

AI Enhance
----------

Preferences > AI Providers has an **AI Enhance** configuration panel. Default:
**Gemini Flash 3.8**, API ID ``gemini-3.8-flash``. It shares the selected Gemini
image connection's key with Nano Banana. There is no second Gemini key field.
With more than one Gemini account, choose the exact account rather than silently
selecting which one to bill. A different Gemini text model ID or an available
connected coding model can be selected. Coding-model enhancement uses the existing
unmodified Codex/Claude adapters in a fresh, text-only context with no host/native
tools, browsing, file operations, scene operations, or cross-agent delegation.
Its instructions (``studio_enhance.EDITOR_INSTRUCTIONS``) are the session's whole
Angelo text on both runtimes: never the chat agent's base, plan or tool text, which
describe scenes, sculpting and views the editor can't use. On Claude Code it adds
one sentence on returning the prompt as the StructuredOutput reply; a Codex thread
replies in plain text. Every text-only use of these runtimes (the editor, the
parts check and the fit) is registered in ``studio_enhance.OWN_TEXTS`` with both its
instructions and that Claude reply sentence, so none can fall back to the agent's
text or miss where its answer goes. ``test_native_studio_editor_prompt.py`` pins
every use's launch on every coding connection.

Enhance is explicit and confirmed before using the account. Without a usable
Gemini key (or with the chosen enhancer missing) the click refuses: the reason,
such as *Add a Gemini API key in AI Providers*, is the Studio's notice, and the
Create panel's **Connect in AI Providers** is right there. Every Studio click
runs through ``studio_state.refusable``: an operator that refuses raises its
report out of ``bpy.ops``, which would otherwise escape the input handler as a
Python traceback and end the Studio's input; instead the reason becomes the
notice unless the operator set one, and the Studio keeps taking clicks. ``test_native_studio.py`` and
``test_blender_generation_connect.py`` (a real click with no account, then
another click and Connect in AI Providers, which opens AI Providers) cover it.
Original prompts are not overwritten automatically. Review the result in the expanded multiline
editor, Apply, or Discard. Undo prompt enhancement restores the prior draft.
The expanded editor only occupies the Media Studio area, never the 3D viewport.

Pointer and native-tool safety
------------------------------

The canvas never grabs, confines, or warps the system cursor. Studio and chat
input handlers yield to any foreground native modal operator (Scale, Move,
Rotate, property drags, and similar tools), including Escape and mouse releases.
Startup docking and the Studio launcher wait for that interaction to finish;
opening Studio programmatically during an active tool is rejected rather than
replacing the viewport underneath a grabbed cursor. Selection dragging ends even
when the release occurs outside the editor. Re-enabled host generations invalidate
old background handlers so they cannot steal or duplicate input events.

``test_blender_input_capture.py`` covers real native Scale, Escape through a
focused chat handler, safe deferred launching, and pointer/click passthrough to
other editors. No provider usage or scene changes are committed by this test.

Clickable states
----------------

Everything clickable on the custom-drawn canvases (the chat, its welcome card,
notices, approval and snapshot cards, the welcome screen, and Studio) looks and
feels clickable, in the console's button language (``canvas_controls.py``).
Canvases still register clickables as ``(rect, action, value)`` hits; the shared
module draws the states over them. Hover lays a faint mint-white layer and a
brighter hairline over a control, or darkens a light control (the primary
Approve) and gives it a mint outline; the pointer becomes a hand. Pressing
darkens the control from mouse-down until release, and a release off it does
nothing. Tab and Shift+Tab move keyboard focus through the controls in reading
order with the console's 2 px mint ring; Return or Space activates the focused
control, and Escape or typing returns to the text field. A disabled control is
registered with the reason (``canvas_controls.DISABLED``): the empty Send button
and a run card's "Nothing selected" show the not-allowed pointer and say why.
Links (More options in Preferences, links in replies) brighten and underline
instead of getting a layer. Layers follow each control's own corners and fill,
recorded as the canvas draws its shapes.

Hover redraws only when the control under the pointer changes; nothing polls.
Blender's console resets its text cursor on every mouse move it receives, so the
chat and Studio consume moves over a control to keep the hand. The connect rows
all use one ghost style; a sign-in in progress turns its row teal with a
spinner and a Cancel button (the team row too, ``team_chat.welcome_row``'s
``busy`` and ``cancel``), redrawing only while it runs and staying still with
Reduce Motion. The chat canvas has one dark palette; there is no light theme.

``test_blender_canvas_states.py`` proves hover, pressed, keyboard focus,
activation, the running state with Cancel, the disabled Send reason, a narrow
chat and the welcome screen with screenshots and brightness checks;
``test_blender_chat_cards.py`` adds the notice actions and Approve.

Following async changes
-----------------------

Work that finishes off the main thread (a team sign-in, a CLI sign-in check, a
connection found, a seat, key or policy change from the console) reaches the
chat through ``ui.pump``, the main-thread timer: each such change bumps the
chats' versions (``console.redraw_chats``) and AI Providers redraws, so the
chat shows it on the next tick, about 0.1 s, with no input and no polling in
a draw. Blender removes a timer the first time it raises, which would stop
the chat following anything for the rest of the session, so the pump is
guarded (``pump_guard.keep_alive``: the error is reported once and the timer
runs again), and the team account's part is isolated so an unexpected console
answer can't hold up the rest. ``test_blender_team_chat.py`` completes a sign-in
from a background "browser" and checks the chat changed within a tick.

Generation and library
----------------------

Image endpoints: OpenAI Images generations/edits (Flare/Sunburst), Gemini
Interactions (Nano Banana 2.1/2/Pro), and Seedream 5.0 Pro. 3D: Tripo v3 P2 and H3.1,
Meshy 7/7.1. Tripo H3.1 uses the documented v3 wire ID ``v3.1-20260211``;
``tripo-v3.1`` was a legacy/v2 alias, not the new endpoint's model identifier.
Controls are capability-scoped: OpenAI quality/background/custom size, Gemini
aspect/resolution, and 3D triangle target/polycount/textures. Unsupported controls
are not sent as invented flags. No prices are fabricated.

Gemini image models, in catalog order: Nano Banana 2.1
(``gemini-nano-banana-2.1``, the default, which Google recommends for new
projects), Nano Banana 2 (``gemini-3.1-flash-image``) and Nano Banana Pro
(``gemini-3-pro-image``). A model is usable only when the key's model listing
includes it, so a key without 2.1 keeps Nano Banana 2 as its default.
``GEMINI_IMAGE_CONTROLS`` in ``provider_registry.py`` records each model's
documented values. 2.1 offers 1K/2K/4K; 2 also offers 512. Both support 1:1,
2:3, 3:2, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9 and 21:9, plus the panoramic 1:4, 4:1,
1:8 and 8:1. Pro offers 1K/2K/4K without the panoramic ratios.
Studio and the chat tool offer the subset 1:1, 16:9, 9:16, 4:3, 3:4 at
1K/2K/4K. That subset is valid for every model, and validation refuses a value
outside the chosen model's list before any upload or billing.

Gemini generation is ``POST /v1beta/interactions`` with ``store: false`` and
``response_format: {type: image, mime_type: image/jpeg, aspect_ratio,
image_size}``. The API reference's ``ImageResponseFormat`` lists ``image/jpeg``
as the only output type. The guide's examples also show ``image/png``, but the
live API rejects it with HTTP 400 for ``gemini-3.1-flash-image``. Every Gemini
image model therefore requests JPEG. The provider's JPEG bytes are kept as they
are and saved as ``image-N.jpg``: no lossy re-encode, no larger PNG copy.
Thumbnails, previews, Use as reference, Import and Export already accept JPEG,
and exports keep the ``.jpg`` extension. The image is read from the
``model_output`` step; thought steps are ignored. ``output_image`` is an SDK
convenience property, not part of the REST schema, and is accepted only as a
fallback.
AI Enhance uses the same endpoint with text input, ``store: false`` and
``generation_config`` (``thinking_level``, ``max_output_tokens``), without a
``response_format``. Sources:
`Gemini Nano Banana 2.1 <https://ai.google.dev/gemini-api/docs/models/gemini-nano-banana-2.1>`__,
`Gemini image generation <https://ai.google.dev/gemini-api/docs/image-generation>`__
("Aspect ratios and image size"),
`Interactions API reference <https://ai.google.dev/api/interactions-api>`__ and its
`OpenAPI description <https://ai.google.dev/static/api/interactions.openapi.json>`__
(``ImageResponseFormat``, ``GenerationConfig``, ``Interaction.steps``).

Generate shows the named account, API billing, image uploads, and output intent
before submitting. Textured Meshy text-to-3D explicitly discloses two billable
stages (preview + refine). All inference and polling happen on workers; bpy and
library/UI updates stay on the main thread. Four concurrent jobs maximum. POSTs
are never automatically retried. Stop cancels local monitoring, not a guarantee
that a submitted remote task stops processing or billing. Provider task IDs and
progress are durably journaled without credentials or signed download URLs.

Outputs are saved locally, not automatically inserted into a scene. Folders,
search, Import images, Use as reference, Export, and explicitly confirmed native
GLB import are available. 3D import supports Blender Undo. Downloaded GLBs must
be self-contained; external file/network dependencies are rejected. Image byte
size and decoded dimensions are bounded before thumbnail loading. Verified TLS,
public-address DNS pinning, fixed service hosts (plus the configured Angelo
gateway origin for team connections), no auth redirects, and no keys or tokens
forwarded to asset CDNs protect provider credentials.

**Team connections** (``ai_providers.rst``, "Team account and team
connections") appear in the model dropdown under the organization's name while
the person is signed in to an Angelo team. Their requests go to the provider's
route on the organization's Angelo gateway with the person's in-memory access
token (``Authorization: Bearer``), renewed for every request, so a long 3D job
outlives the 10-minute token; the gateway applies the organization's own
provider key. Request formats, controls and outputs are those of the personal
service (``generative.GenerationBinding.service``); the Generate confirmation
says the team's key is used. Plain http reaches the gateway only when
``ANGELO_GATEWAY_URL`` names a loopback host (development). A gateway
``UNAUTHENTICATED`` renews the token and resends that one request (it never
reached the provider); a ``RATE_LIMITED`` or ``UNAVAILABLE`` status poll waits
and keeps monitoring the submitted task. Downloads stay direct and
unauthenticated.

Library data defaults to the app-wide ``media-studio`` directory beside Angelo's
durable ``connections`` directory. It is independent of transient chat/scene
state. ``ANGELO_LIBRARY_DIR`` is an absolute override for test/isolated libraries;
``ANGELO_STATE_DIR`` does not move or erase library/account data. Each asset and
folder has its own atomic metadata record, not a stale all-library overwrite.
Refresh reloads changes from another instance. API keys live in memory and,
when Remember is on, in the OS key store (``ai_providers.rst``); team access
tokens live in memory and the OS key store (``ai_providers.rst``, *Refresh
cadence*); the library contains no keys or tokens.

Media Studio in the Asset Browser
---------------------------------

Since 1.0.5 the library is also a Blender asset library, **Angelo Media Studio**
(Preferences > File Paths > Asset Libraries). Generated models and images are in
the Asset Browser and in every asset shelf that lists objects or materials, and
drag into a scene like any asset: Link, Append, Append (Reuse Data), Pack and
collection instances all work as they do for any other library. Blender's Add menu
lists node-group assets only, so it shows none of them. Blender lists datablocks
inside .blend files, so every ready item gets its own .blend in
``<library>/blender-assets`` (``models/`` and ``images/``, named by the item's id),
with a ``blender_assets.cats.txt``.

**Disk.** A model's .blend holds the model itself, compressed: about 40% of its
GLB, so about 3.8 MB for a 9.6 MB provider model, or 3.8 GB per 1,000 of them
(measured below). An image's .blend holds a preview and a material that
references Studio's picture, about 130 KB; the picture is not copied. The library
adds to Studio's own outputs and never changes them.

Media Studio shows the library and its size at the foot of its Folders column
(*Asset Browser · On · 1.9 GB*; in a narrow editor, in the folder picker), with
its controls (``studio_library_ops``, files and rules in ``studio_assets_space``):

* **Turn off** asks first, in an Angelo card, with *Also delete its copies* and
  the warning that a scene which links a model from this library shows it as
  missing until the library is turned on again, while appended and packed items
  are not affected. *Also delete* starts ticked, except when the open file links
  models from the library or the library's entry imports by linking (the card
  says so). It removes the Preferences entry, as removing it there does, and a
  running write stops within a quarter second. Removing the entry in Preferences
  asks the same question there.
* **Free up space** (only while off) deletes the copies after the same warning.
  Nothing is deleted without the card's confirm: it passes a one-shot token the
  operator consumes, so an ``EXEC_DEFAULT`` call of either operator, from a
  script or an agent, frees nothing. It deletes only files
  Angelo named itself in its folder: each item's ``<id>.blend`` in ``models/`` and
  ``images/``, a writer's leftover ``<id>.blend.<suffix>.tmp``, and the manifest.
  Regular files only, one folder level, never through a link or a Windows
  junction (the folder itself, a catalog folder or a file). A file the person put
  there stays, and so do the catalogue file (it keeps catalogues the person
  added) and ``registration.json`` (the library stays off). It runs on a worker
  thread, never blocking Blender, only while this Angelo holds the writer's lock
  (it waits up to three seconds for its own writer) and the library is still off
  (read again under the lock); then the status bar says how much it freed, that
  the library is busy, or that it is on again and nothing was deleted. Its report
  survives a file load, and a failed start never leaves it unavailable.
* **Turn on** adds the entry again; the writer writes every copy again.

The size is measured on a worker thread (a stat per file), at most every 30
seconds while the row is drawn; a free makes a measure that began before it stale,
so an old total never shows afterwards. An entry that comes back after Turn off
(a crash or a quit before the preferences were saved, or another Angelo saving
them) turns the library on again, so it is never "On" without writing.

* **A model** becomes an Object asset (one object) or a Collection asset (several),
  catalogue *Models*, holding the model: the GLB imported in the background writer,
  textures packed, compressed (``studio_assets_mesh``). One object is baked to
  identity with its origin at the base centre of its vertices' bounds; several keep
  their places and the collection's instance offset is that base centre. A drop
  stands the model on the surface under the pointer, at the GLB's own size; a
  collection instance goes there too. Linked, it is the library's, as any linked
  asset: a linked object cannot be moved, so Blender puts it at the origin, standing
  on it; it follows the asset file; a file that links it is moved with Save As, which
  keeps relative library paths. An import that fails part-way leaves
  nothing in the writer, and that item alone fails. The preview is the provider's
  picture, or a Workbench render when there is none. The model is written and
  reported before that render: where a background Blender has no usable GPU context
  (Blender's OpenGL backend counts as supported, so the render ends the process),
  the model stands without a preview, no other item is blamed, and every later
  writer of that session (the sync and a drag's write at once alike) renders
  nothing; a writer that ends early prints its last output to the console. The
  manifest records a model left without its preview (a render that failed or ended
  its writer, or renders off), and each later session writes it again once, so it
  gets its preview once the machine can render (``test_blender_studio_previews``
  forces the failure with ``ANGELO_QA_FORCE_PREVIEW_FAILURE``). A render with
  nothing to show (no mesh) is not tried again.
  ``test_blender_studio_assets``
  links, appends and instances each model through Blender's own operators and checks
  the evaluated scene (mesh count, vertices, size) after save, reopen and move. A
  rewrite keeps links while the asset's name and type stay; a replaced model that
  turns one object into several becomes a Collection asset, and a scene that linked
  the Object then shows it missing.
* **An image** becomes a Material asset (Principled BSDF, Image Texture on Base
  Color and Alpha), catalogue *Images*, with the picture as its preview. A Material,
  not an Image: Blender 5.2's Asset Browser lists Image datablocks only under the
  experimental Extended Asset Browser, and a material dropped on an object textures
  it. The library references Studio's own file by its absolute path and stores no
  copy; the picture carries a saved ``angelo_studio_asset`` property. When the
  material lands in the person's file (a drop or an append in an interactive
  session; a background ``-b`` Blender registers none of this), Angelo packs the
  pictures that import brought (``studio_assets_landing``: ``blend_import_pre`` notes
  what was there, ``blend_import_post`` packs during the append itself, so the drop's
  undo takes it back). Each picture is packed once: its ``angelo_studio_asset`` marker
  is then replaced by ``angelo_studio_packed``, so an Unpack, repack or paint the
  person makes later stays theirs. Saving packs only what an import could not (Studio's
  file was missing then), and never in the library's own files. Nothing is
  freed inside the import (``bpy.data.libraries.load`` hands its IDs to the script
  afterwards); a duplicate picture is remapped and freed from a timer if unused. A referenced
  picture alone would not survive: appended into an unsaved file, Blender's first
  save makes its path relative, and moving or renaming the file then loses it. Packed,
  it survives every save, reopen, move, rename and share, and the person's file holds
  only the pictures it uses. Angelo creates the entry with the import method
  *Append* (5.2's default, *Pack*, links a packed copy of the asset file, whose
  picture would still point at Studio's folder); a person who changes it later
  keeps their choice, and a linked picture stays the library's. A second drop of the same material reuses the packed
  copy only while it is unchanged (same bytes as Studio's file, nothing painted,
  and the same colour space, alpha mode, source and name);
  otherwise it packs its own copy, as stock Blender keeps a second picture
  (``test_blender_studio_assets``: ``texture_*``, ``dropped_twice_one_copy``,
  ``an_unpack_stays_unpacked`` and ``a_second_drop_keeps_studios_picture`` check
  pixels, not paths).
* **Catalogues**: a fixed, untranslated parent ``Angelo Media Studio``
  (``studio_assets.TAB_PATH``, its own fixed id) holds *Models* and *Images*. An
  Asset Shelf tab is stored by catalogue path, so the parent's path never changes
  with the language; ``studio_assets.catalog_kind(path)`` names the kind of a child
  or of any catalogue the person made under one. The three catalogue ids are fixed
  for good.
* **Name, description and tags**: the name is the prompt's first line (60
  characters), the description the prompt, the tags the model and the Studio folder.
  They are the person's text and never translated; catalogue names follow the
  interface language (the file is rewritten when it changes, the catalogue ids stay).
* **One way, Studio to Blender.** The Studio record is the source. Blender cannot
  rename or delete an asset that lives in another file from its Asset Browser, and
  Studio has no rename or delete of its own. A record that changes (folder move,
  replaced file) rewrites its .blend; a record that disappears removes the .blend
  this folder named for it, and nothing else.

**Dragging Media Studio's items.** An item drags the same way from Media Studio's
grid, the Asset Browser and an Asset Shelf: all three start Blender's own asset drag
(the item under the pointer, plus every selected item as a list). Media Studio's
grid calls ``ASSET_OT_angelo_drag`` (``asset_angelo_drag.cc``) once the pointer
moves past Blender's drag threshold, even when it leaves the tile fast. A release
without moving opens the item, as before. Shift or Cmd/Ctrl+click adds an item to
the selection or takes it out. A drag carries only selected items the grid shows
now, never another tab's or folder's.

An item not yet in the library is written at once first. The wait is at most a
second for this Angelo's writer to step aside; another Angelo writing the folder is
never waited for. An item whose file failed to write is never retried on a drag. In
those cases, and with the library off, the drag carries Media Studio's own files,
and a drop does what dropping those files does: never a box.

==========================  ===============================  ==========================================
Target                      Model                            Image
==========================  ===============================  ==========================================
3D view                     under the pointer, at the GLB's  on an object that takes materials: its
                            size (Blender's drop)            material (Blender's drop); on empty space,
                                                             an Empty, a camera or a light: a reference
                                                             image
Properties, material slot   (Blender: no drop)               the slot's material (Blender's drop)
Node editor                 Geometry Nodes: an Object or     an image node; a Flow's Image step
                            Collection Info node; a Flow:    (space_node's own drops,
                            refused, with the reason         ``node_add.cc``)
Image Editor                refused, with the reason         opens, packed
Outliner                    into the collection of the row   over an object's name: its material;
                            (the parent's at a row's top or  elsewhere refused, with the reason
                            bottom edge; a scene's own); a
                            linked collection: refused
Chat                        its GLB attached                 attached
Media Studio                refused, with the reason         a reference (its own grid's drag: over
                                                             the Create column; elsewhere on it,
                                                             nothing, as an asset dropped back on
                                                             the Asset Browser)
==========================  ===============================  ==========================================

Several items dropped at once all land: models stand side by side across the view,
images as reference images in a row, or as material slots on the object. A drop is
all or nothing: when an item's file is gone, nothing is added and the reason shows.
Each drop that changes the file is one undo step (``angelo.media_drop``), whatever
the window it came from. A drop that only changes the interface (the chat's
attachments, Media Studio's references, ``angelo.media_drop_ui``) pushes none. A
picture lands packed, once, however often it is dropped (a reference, a material,
the Image Editor).

On release Blender runs only the first drop box, in list order, that takes any of
the drags, then frees every drag. So Angelo's boxes come in this order in each
editor: the list (two or more items), the single item, the refusals, a box that
only hides the single drag's tooltip while the list's shows, then the editor's own.
Each copy writes every target field, so what one drop was over never reaches the
next. Angelo's drop boxes (``asset_angelo_media.cc``; the Outliner's in
``outliner_dragdrop.cc``) recognise a Media Studio asset by its catalogue (the two
fixed ids) and run ``ASSET_OT_angelo_media_drop``, which hands the drop to the
add-on (``media_drop.py``, placing in ``media_place.py``) in the drop's own library.
Where a kind makes no sense, ``ASSET_OT_angelo_media_cannot_drop`` refuses: Blender
shows its reason as the drop's tooltip and nothing is added.

Each drag has a menu equivalent, for the keyboard and the agent. Media Studio's
item popup, the Asset Browser's context menu and the Asset Shelf's offer **Add to
Scene** (models standing at the 3D cursor, images as reference images), **Apply to
Selected** (images, as materials) and **Add to Chat**. They are the operators
``angelo.media_add_to_scene``, ``angelo.media_apply_to_selected`` and
``angelo.media_add_to_chat``, which take Media Studio ids. With the library off,
Add to Scene imports the GLB with Blender's glTF importer, shaped as the library's
writer shapes it, and Apply to Selected makes the material from the picture.

Limits:

* A model has no Flow input yet.
* A linked object stays where Blender links it, as with any linked object asset:
  its place is the library's, so a move would not be saved. A linked collection is
  placed through an instance.
* An Outliner drop on an object needs the pointer over its name, as Blender's own
  Outliner drops do.

Tests: ``test_native_media_drop.py`` (contracts, identifiers, the grid's press and
release, writing first, menus), ``test_blender_media_drop.py`` (placement, the
operators, the library off) and ``test_blender_media_drop_gui.py`` (real drags to
every target, refusals, several at once, undo, from Media Studio's own window).

``studio_assets.py`` plans (no Blender dependency); ``studio_assets_convert.py``
writes, in a background Blender (``-b --factory-startup``, priority lowered,
40 items per process, each file written to a temporary name and moved into place);
``studio_assets_host.py`` registers and schedules. Registering the add-on compares
Preferences' libraries with the folder (0.4 ms) and adds the entry when missing,
before any asset shelf draws: adding a library rebuilds Blender's asset lists, and
doing that while a shelf's preview job ran crashed Blender (``PreviewLoadJob``; a
person adding any library while a shelf loads is the same native hazard). A renamed entry is found by its folder. Removed in Preferences,
the removal is recorded (``registration.json``) and it is never added again until
the person adds the folder again; a preference read (factory preferences, Revert, a
settings import, a template; ``prefs_reload.READ_LISTENERS``) is not a removal. The
first scan starts 20 seconds after startup. The main thread only reads Preferences
(library paths compared as text, never on the file system, so a library on an
unreachable share cannot stall it) and, after new files, refreshes the open Asset
Browsers and Asset Shelves that show this library or All (Blender re-reads an on-disk
library only when asked; shelves share one list per library, so one refresh each).
A refresh never runs during a drag: a dragged asset points into the lists a refresh
frees, so Blender crashed when a script refreshed a library while a person dragged
one of its assets. ``asset.library_refresh`` now refuses while a drag is in progress
(``asset_ops.cc``, a change to Blender) and Angelo's refresh waits for the next tick.
A worker thread does every file access: every five seconds two
folder stats (0.03 ms) decide whether the library or the language changed; only then
does it read the records and plan, and only new or changed items are written
(``manifest.json`` holds each item's fingerprint, read at any size). One writer per
folder across Angelo instances (an operating-system lock, never waited for), and the
manifest is read before the records so one instance never takes another's new item
for a removed one. A record that cannot be read is left out, never the whole library.
A copy is deleted only when its Media Studio item was deleted (its record file is
gone) and every record in the folder reads cleanly (``studio_assets.removable``): an
item left out for any other reason (an unreadable or oversized record, not ready, its
model file missing, past the reader's 10,000 records, the folder unreachable) keeps
its copy, because the copy holds the only Blender data of its model; the log names
a damaged record once. "Gone" is proven, never assumed: ``os.path.lexists`` and
``Path.glob`` answer "nothing there" for a folder they can't read, so a records folder
with mode 000 read as every record gone and deleted every copy (release audit of
1.0.4/1.0.5); the check now stats each record strictly and lists the folder so that an
error blocks every removal. Every writer reads the registration again under the lock and
between batches, so a Turn off in another Angelo stops it before its next batch. A real deletion
removes the copy, and a scene that linked that model then loses it on reopen, as with
any linked asset whose file is deleted; an appended model is unaffected.
When the writer dies on an item, that item alone fails and the items after it run in
a fresh process; a failure is retried in the next session (even while an older copy
is still there) or after a change, and a
writer that produces nothing twice waits for the next change. The writer yields to
the person's work: while a render, bake or composite runs, an Angelo background job
runs, or an agent turn is in progress (``studio_assets_host.host_busy``, read on the
main thread every five seconds), it starts no batch and stops a running one; what
it finished counts and the rest waits, never as a failure. Once the library is
removed in Preferences, no Angelo instance writes to it. Known limits: two Angelo
instances in different interface languages rename the two catalogues back and forth;
changing ``ANGELO_LIBRARY_DIR`` (tests) leaves the earlier entry in Preferences.

Measured on Mac 2 (M5 Max, ``nice 15``, Angelo 5.2.2 binary, this add-on), half
models and half images per library; "small" models are a 70 KB GLB, "provider-size"
ones a 9.6 MB GLB (about 200,000 triangles, a 2048-pixel texture); images are a
300 KB picture. The first write happens once, in the background child; afterwards
only new or changed items are written. 1.0.4 had no asset library, so all of
this is added disk use.

============================  =========  ==========  ============  ============
Library                       First      Asset       Studio        Re-scan,
                              write      folder      outputs       no change
============================  =========  ==========  ============  ============
1,000, small models           32.1 s     111 MB      192 MB        81 ms
1,000, provider-size models   142.1 s    1,982 MB    4,964 MB      89 ms
============================  =========  ==========  ============  ============

A provider-size model's .blend averages 3,835 KB beside its 9,614 KB GLB (40%); a
small model's 92 KB (its preview outweighs a 70 KB GLB); an image's 130 KB, its
preview and material: the picture stays Studio's. The registration check costs
0.4-0.6 ms once on the main thread. The five-second check (0.02-0.03 ms) and the
re-scan, which runs only after a change, are the worker thread's.

``test_native_studio_assets.py`` (plan, catalogue, registration rules, the startup
path, batches, the lowered and stoppable writer), ``test_blender_studio_assets.py``
(the real Preferences entry and writer, sizes, base, paths, one-way sync) and
``test_blender_studio_assets_gui.py`` (the previews by their pixels in an Asset
Browser on the library, real drags of a model onto the floor, appended and linked,
and of an image onto an object).

Models with morph targets
-------------------------

Blender's glTF importer keeps a file's split seam vertices apart unless Merge
Vertices is on, and it cannot keep glTF morph normals (shape keys store none).
Once a shape key moves the surface, each split copy of a seam vertex averages
only its own side's faces, so every seam shades as its own patch; three.js,
which uses the file's morph normals, shows none. A person importing kit GLBs on
1.0.2 reported it, and stock Blender 5.2 does the same.

Maintainer decision (2026-10-23): Blender's import default stays as it is, so
people's scripts and pipelines get the meshes they always got.

* **The importer warns.** ``io_scene_gltf2`` reports a warning when a file with
  morph targets is imported with Merge Vertices off: on the console, which a
  script's output (and so an agent's) captures, and in the status bar,
  translated through the Angelo catalogue (``sources.EXTRA_MSGIDS``). An agent
  learns it exactly when it imports such a file, at no prompt cost on every
  other turn.
* **Angelo's own paths weld.** Every ``import_scene.gltf`` call the add-on makes
  passes ``gltf_morph.import_options(path)``: Merge Vertices when the file's
  glTF JSON has a primitive with ``targets``, nothing otherwise, so every other
  file imports exactly as Blender's default does. That covers Media Studio's
  Add to Scene and drags (``media_place`` through ``studio_assets_mesh``), its
  asset library's models, the agent's ``angelo_studio_import_3d`` and Flow's
  Place in Scene (``studio_host``), the Library, and the team library.
  ``test_native_gltf_morph.py`` fails if a new call skips it.
* Merge Vertices keeps a model's own hard edges: the importer merges only
  co-located vertices with the same normal.

Keeping the morph normals themselves needs per-shape-key normals in Blender's
data model, which is upstream work. ``test_blender_gltf_morph_weld.py`` writes
fixtures (a UV sphere, and held out from the fix's development a twisted torus,
a bent cylinder, and a file with a morph mesh beside a flat-shaded one). For
each, it checks that the default still splits the seams and warns, that seams
shade apart only after the shape key moves, that the options and the model path
weld them so none does, and that hard edges stay. A plain file gets no option
and no warning.

Provider errors
---------------

When an admitted service answers with a non-2xx status, or with a 2xx body that
carries an error, the user sees ``<Service> rejected the request (HTTP <status>):
<LABEL> — <message>`` (401/402/403/429 keep their guidance and append
``<Service> said: …``). The same text is the Studio asset's failed status, the
chat tool result and the AI Enhance notice. ``provider_errors.py`` owns this.
It reads at most 16 KiB and only structured JSON fields: Google's
``error.status``/``message``, the Interactions, OpenAI and BytePlus
``error.code``/``type``/``message``, and Tripo/Meshy top-level
``code``/``message``. The text is untrusted. The request's exact credential
values, key/token/Bearer/``key=`` shapes, long opaque identifiers, URL
login/query/fragment parts and control characters are removed, and it is
truncated to 300 characters. Raw bodies, request bodies and keys are never
logged, shown or persisted. Asset/CDN download errors don't read the body. A
rejected request is never retried automatically.

Validation and preview
----------------------

Pure tests: ``test_native_studio.py``; provider-error extraction, redaction,
truncation and the failed-asset status: ``test_native_provider_errors.py``.
Native GPU/operator acceptance:
``test_blender_studio.py`` (stock and compiled fork). Its provider calls are
explicit local fixtures: generation confirmation, output files/gallery,
enhancement confirmation/review/undo, model selection, tabs, collapse, and Python
isolation. Its Gemini fixture answers with a JPEG, as the live API does, and the
suite checks the ``image/jpeg`` request, the saved ``image-1.jpg`` and its drawn
library thumbnail. Fixture captures are not proof of live provider access or paid output.
Live provider generation requires the user's own GenAI API keys and model access.

``test_native_director_studio.py`` covers tool schemas, capability and account
validation, attachment ownership, no-auto-billing/read-only policy, deferred
completion, key-rotation approval invalidation, library operations, previews and
cancellation with local fixtures. ``test_blender_director.py`` exercises actual
default startup, Scene-adjacent switching, latest-draft transfer,
chat-only generation/enhancement/library/export, separate native GLB import and
Undo, with no Studio editor open and no live provider access.
``test_blender_director_navigation.py`` sends real window events for every
navigation, selection and transform input in Director and checks the result.
``test_blender_angelo_startup.py`` covers remembered Manual startup and explicit
Studio editor launch. GUI receipts require ``success=true`` and ``phase=complete``;
normal application quit alone is not a passing test.

Launch a separate preview with the repository's ``launch_studio.py`` script::

   /path/to/Angelo.app/Contents/MacOS/Angelo --factory-startup \
     --python scripts/angelo/launch_studio.py

This opens the Media Studio editor in the current workspace, with Coding Agent
docked outside Properties on the right. It creates no workspace/tab and does not
reset connections or generate any assets on startup. Normal Angelo GUI startup
opens Director preview and Coding Agent on the right, or the remembered Manual
layout, without needing a preview script. Explicit Studio preview launch returns
to Manual mode first. Background
runs skip that setup; test automation can opt out with
``ANGELO_DISABLE_DEFAULT_CHAT=1``.

Asset Browser: empty and no-results states
------------------------------------------

An Asset Browser whose list shows nothing says why and offers Media Studio, only when that is true
(``asset_browser_states``). The counts are Angelo's own RNA on ``SpaceFileBrowser``, read-only and
never saved: ``angelo_listed_count`` (entries shown after search, catalog and type filtering),
``angelo_total_count`` (entries read before filtering) and ``angelo_list_ready`` (fully read and
filtered since the list last changed), from ``ED_fileselect_angelo_list_counts`` (``filesel.cc``)
and ``filelist_files_num_entries_filtered``. Nothing is drawn while the list is still being read,
when anything is listed, or on a build without the counts.

- **Empty** (nothing listed, no search typed: an empty library or catalog): Media Studio's 32 px
  spark-frame glyph, "Nothing here yet" (``TYPE_HEADING``) and a teal **Generate in Media Studio**
  (``BUTTON_HEIGHT``, ink text). Media Studio's own library (part E's, found by its name in the
  Preferences, which the person may change) says "Your generated images and models appear here"
  instead, as its empty Asset Shelf does.
- **No results** (the library has assets, the search lists none): "No matches for "{query}"", a
  **Clear search** pill (``PANEL`` fill, 1 px ``LINE`` border, ``BUTTON_HEIGHT_SMALL``) that clears
  only the search, then **Generate in Media Studio**.

The block is centred in the list, at most 360 px wide (× UI scale); every line, button labels
included, wraps and is never cut. Buttons light up under the pointer. Generate opens Media Studio
through the shared opener with the browser's kind (``angelo.open_media_studio``, AUTO). The header
pills stay in both states. ``test_blender_asset_browser_states`` checks the counts, both states and a
list with assets that draws neither, in pixels, in English and German at UI scale 1 and 2.

Asset Browser before its first refresh
--------------------------------------

A File or Asset Browser gets its file list (``SpaceFile.files``) at its first refresh and its runtime
data (``SpaceFile.runtime``) at its first init. A browser just made, read from a file or split off
another one has neither for a moment, and notifier listeners, the Python API and broadcasts over every
browser can reach it then. Each of them treats a missing list as "not read yet" and ensures or checks
the runtime data (``file_runtime_ensure``): the language listener skips the reset (the first read is in
the new language), a job-finished notifier finds no reload callback, ``activate_asset_by_id`` defers
until the list reads, the download broadcast skips the browser, and the Delete and OS-operations polls
answer false, and every file operator that reads the list requires it in its poll ("The file list has not been
read yet"; a directory change without a list only sets the directory). Each was a SIGSEGV on a null pointer in Blender 5.2 (one in the top-bar suite's Asset
Browser sweep). An immediate activation filters the list before it searches it (a threaded read delivers unfiltered entries). A
deferred activation lands once the list is final: a finished read (any browser's)
only marks the space, and its next refresh runs the callback when its own list is read and filtered
(a threaded read's notifier comes before the refresh that filters what it delivered, and filtering
resets the active file); the callback is cleared before it runs, so one that must wait registers
again; a deferred relative path is the space's own copy, since the caller's text (a Python string) is
gone by then. Changing a browser
between File and Asset mode exits the old mode's data (upstream 272b114d07b).
``test_blender_file_browser_before_refresh`` runs the three a timer can trigger in the app;
``test_native_file_browser_uninitialized`` pins the rule in the source.

