.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
.. SPDX-License-Identifier: GPL-2.0-or-later

==========================================================
Entry points into Media Studio and Generation Flows (1.0.5)
==========================================================

This document is the binding contract for every place that opens Media Studio or a Generation Flow:

* the top bar, Manual's workspace strip and the **+** Add Workspace menu;
* the Window menu, the Angelo menu and the chat's **+** menu;
* the welcome card and the chat suggestions;
* the Asset Browser, the Asset Shelf and the asset library;
* the links between Media Studio and a flow.

Together they must read as one designed system. The decisions rest on the 1.0.5 captures
(``test_blender_studio_discoverability``, ``test_blender_chat_start_states``) and on
``feature/v1.0.5-studio-discoverability``. The maintainer's explicit decisions override this document. A change to
an entry point follows this document, or changes it first.

0. The model: start, then go further
====================================

**Media Studio is where you start**: one-shot generation of an image or a 3D model. **Generation Flows is where
you go further**: the same generation as a chain of steps you can rerun.

* Media Studio always comes first and Generation Flows second: in the top bar, the workspace strip, every menu,
  the **+** menu, the Add Workspace menu and the Asset Browser and Shelf headers.
* One description per concept, word for word, wherever a why line or tooltip appears:

  * Media Studio: "Generate images and 3D models in one step"
  * Generation Flows: "Go further: chain generation steps into a flow you can rerun"

* From Media Studio, the step onward is **Turn into a flow**. In a flow, the way back is **Media Studio**.
* Flows are never visually louder than Media Studio: the same component, the same size, with Media Studio first.

1. Vocabulary
=============

.. list-table::
   :header-rows: 1
   :widths: 22 30 48

   * - Concept
     - Name
     - Short form allowed
   * - The generator
     - **Media Studio**
     - **Studio**, only as the top-bar button label: it sits beside *Chat* in a bar short of room in 21
       languages, and its tooltip gives the full name. Text that points at that button may quote it.
       Everywhere else, including the workspace tab, Studio's own window title, the editor-type menu
       (*AI ▸ Media Studio*, with the spark-frame glyph as built-in icon ``ANGELO_MEDIA_STUDIO``) and the Asset
       Browser and Shelf pills, use the full name. Before 1.0.5 the editor was called *Generative AI*; no
       interface text says that any more. The AI Providers category of image and 3D API keys is **Media
       generation** ("Image and 3D providers used by Media Studio and Generation Flows").
   * - The node workflows
     - **Generation Flows** (the feature); **flow** (one of them, lowercase in sentences)
     - **Flows**, only as the top-bar button label. The workspace tab and the pills use **Generation Flows**.
       The menu item is **New Flow** (Blender's title case for menu items). Media Studio's button is
       **Turn into a flow**.
   * - The Blender asset library of Studio output
     - **Angelo Media Studio**
     - The only name with the *Angelo* prefix, because it sits in Blender's own lists of sources beside
       *Essentials* (Preferences ▸ File Paths, the Asset Browser library menu, the shelf tab), where a bare
       name would not say where it came from. *Angelo Chat* in the Window menu follows the same rule. Its
       catalogs are **Models** and **Images**.
   * - Media Studio's tabs
     - **Image**, **3D**
     - "on its 3D tab", "on its Image tab".

**Translation.** *Media Studio* and its top-bar form *Studio* are a product name. Like *Angelo*, *Claude* and
*ChatGPT*, they stay in Latin script in every language (``locale/angelo/GLOSSARY.md``). *Studio* is looked up in
the ``"Angelo"`` msgctxt so that Blender's lighting term "Studio" never replaces it. *Generation Flows*, *Flows*,
*New Flow* and every tooltip and why line are descriptive text, translated in all 20 catalogues. A descriptive
label that draws in English in another language is a bug. For example, *Flows* draws in English in Arabic in
``first-run-0-setup-light-ar.png`` at ``a7b3667e0c2``, although ``ar.po`` translates it.

**Right-to-left (Arabic).** Blender does not mirror its layout. File…Help stay at the left, so the Angelo group
stays at the right in the same order: Chat · Studio · Flows · mode menu. In each button the icon stays left of
its label, and compacting drops labels in the same order as in left-to-right languages. A shortcut keeps its own
order inside an Arabic label: ``⌃⌥N``, never ``N⌥⌃``.

Verbs: *Open* for a window, *New* for a blank flow, *Generate* for making media, *Import* for files, *Turn into*
for converting a Media Studio setup into a flow. Never "Add asset": in Blender, adding to the asset shelf means
*Mark as Asset*.

Tooltips say what opens and where, and never list features the destination does not hold.

* **Studio / Media Studio**: "Media Studio: generate images and 3D models in one step". While pressed: in
  Director, "Close the Media Studio window"; in Manual, "Back to {workspace}".
* **Flows / Generation Flows**: "Generation Flows: go further, chain generation steps into a flow you can rerun".
  While pressed in Manual: "Back to {workspace}".
* **Turn into a flow**: "Start a new flow from this Media Studio setup: its model, prompt and references".

Flow node gate, neutral and never red before a real failure:

* "Connect an account to run", with the button *Connect in AI Providers*.
* "Choose a model to run".

Red (``CORAL``) and "Can't run" appear only after a run has really failed.

Asset Browser states:

* Empty library: "Nothing here yet", with the button **Generate in Media Studio**.
* No results: "No matches for "{query}"", with the buttons **Clear search** and **Generate in Media Studio**.

2. One icon per concept
=======================

.. list-table::
   :header-rows: 1

   * - Concept
     - Icon
     - Used by
   * - Media Studio
     - Picture frame with a four-point spark (``studio_toggle.icon_id``): outlined while not shown, filled while
       shown.
     - The top bar; the Window and Angelo menus; the **+** menu tile; the Add Workspace item; welcome slide 2;
       the Asset Browser and Shelf pills; the empty-state buttons; the Flow bar's *Media Studio* link.
   * - Generation Flows
     - Three-node glyph (``flow_open.icon_id``)
     - The top bar; the Window and Angelo menus; the **+** menu tile; the Add Workspace item; the Asset
       Browser and Shelf pills; the Flows popup header; Media Studio's *Turn into a flow*.
   * - New Flow
     - ``ADD``
     - The Flows popup, the Flows submenu and the Generation Flows start page.
   * - A template or one of your flows
     - None. The section header already says what the rows are, and the stock ``NODETREE``/``NODE`` icons are
       Blender's look, not Angelo's.
     -

Workspace tabs carry no icon, like every Blender workspace tab. The interim welcome tile's disc draws a **+**
(``studio-7-welcome-en_US.png``). Until the slideshow replaces that tile, it must draw Media Studio's spark-frame
glyph.

3. Behaviour
============

**One home per destination, per mode.** In **Manual** the home is the workspace tab. In **Director**, which shows
no workspace strip, it is the destination's own window. Every entry in a mode goes to that mode's home, so the two
never compete. ``open_media_studio(kind)`` and the flow opener route by mode in one place.

**No entry edits a layout.** Switching to a workspace tab is navigation. No area is split, swapped or retyped in
the workspace being left, and a hidden chat stays hidden.

**No entry acts mid layout change.** Manual's tabs are built once the start-up is quiet; the build switches the
window through the tabs it makes for a few event-loop passes and holds ``ui._layout_timer`` while it does, as
Director's start-up and a chat dock do. A Studio or Flows entry used in those passes (top-bar button, menu, pill,
F3) runs once the layout has settled (``layout_after``), like the Chat toggle; it is never refused. Without this, a
Studio click in Director during the build read the transient Manual workspace and left the window on the half-made
*Media Studio* tab.

**The Studio window keeps what you were doing.** It follows the main window's workspace (Blender's rule for
child windows), so a mode switch or Manual's tab build shows it another screen with another area. Its tab, draft and
references belong to the window, not the area (``studio_views``), so they stay, and switching back finds them.
Opening a Studio window starts it fresh, even at the address of one closed before; every other Studio area keeps its
own state; states of closed windows and freed areas are dropped; a file load, its windows kept or not, carries
nothing over. Studio's pump is a persistent timer, so a file load never stops it (``test_blender_studio_mode_switch``).

.. list-table::
   :header-rows: 1
   :widths: 22 30 48

   * - Entry
     - Manual
     - Director
   * - Top bar **Studio**
     - Switches to the *Media Studio* tab. Pressed while that tab is active; a click while pressed goes back
       to the workspace you came from.
     - Opens the Media Studio window. Pressed while that window is open; a click while pressed closes it, as
       Chat does.
   * - Top bar **Flows**
     - Switches to the *Generation Flows* tab. Pressed state and toggle work as for Studio.
     - Opens the Flows popup (owner A) under the button: **New Flow**, then *Start from a template*, then
       *Your flows*. A picked row opens that flow in its own window.
   * - A workspace tab clicked in the strip
     - Same as the matching button; the button's pressed state follows.
     - Not applicable (no strip).
   * - Window ▸ or Angelo menu ▸ Media Studio; chat **+** ▸ Media Studio; welcome slide 2 *Open Media Studio*
     - The *Media Studio* tab. Never toggles back.
     - The Media Studio window. Never closes it.
   * - Window ▸ or Angelo menu ▸ Generation Flows ▸ (New Flow, templates, yours); chat **+** ▸ Generation Flows
     - The *Generation Flows* tab, showing the picked flow.
     - The picked flow's own window.
   * - Asset Browser and Asset Shelf header pills: **Media Studio** · **Generation Flows**
     - Media Studio: the *Media Studio* tab, on the tab the context names. Generation Flows: the
       *Generation Flows* tab.
     - Media Studio: its window, on that tab. Generation Flows: the Flows popup, anchored under the pill.
   * - Asset Browser empty state and no-results state: **Generate in Media Studio**
     - As the Media Studio pill.
     - As the Media Studio pill.
   * - Asset Browser no-results state: **Clear search**
     - Clears the browser's search field and nothing else.
     - Same as Manual.
   * - Media Studio ▸ **Turn into a flow**
     - A new flow pre-filled from this setup, in the *Generation Flows* tab.
     - That new flow's own window.
   * - Flow bar ▸ **Media Studio**
     - The *Media Studio* tab.
     - The Media Studio window, brought forward.
   * - **+** Add Workspace ▸ Angelo ▸ Media Studio or Generation Flows
     - Switches to the existing tab. Adds the workspace only when the file has none; never creates a ``.001``
       duplicate.
     - Not applicable (no strip).

**Media Studio tab under AUTO** (``studio_toggle.studio_kind``; owner D, in ``open_media_studio``):

* The first time Media Studio is shown in a session, the tab comes from the context the person came from,
  computed before switching. It is Image for the Image editor, shader or texture nodes, material or texture
  properties, Texture Paint, or a material, image or texture asset; otherwise it is 3D.
* After that, general entries keep the person's current tab. A menu must not overrule a choice they just made.
* Contextual entries (the Browser and Shelf pills, the empty states) always select their tab.

**The workspaces** (owner studio-disc):

* *Media Studio* shows Media Studio full-area. *Generation Flows* shows the flow editor full-area.
* Neither has a stock header, sidebar or editor-type menu. The global top bar and the status bar stay.
* Strip order: Director · Media Studio · Generation Flows · Layout · …
* Both tabs are in new files, and are added on load to files that lack them. A tab the person deleted in a file
  is never re-added to that file; a flag on the file records the deletion.
* With no flow open, *Generation Flows* shows a branded start page. It has the Flows popup's rows from the same
  registry, drawn with the same row component.

**+ Add Workspace** (``WORKSPACE_MT_add``, drawn in C in ``workspace_edit.cc``) gets an **Angelo** section after
*Search…* and above *General*, with **Media Studio** and **Generation Flows** and their glyphs. The C code draws a
Python ``ANGELO_MT_workspace_add`` through ``menu_contents``, so labels, icons and translations stay in the
add-on.

**Before an account is connected**, every entry still navigates; navigation is never gated. The gate lives once,
inside the destination: Media Studio's *Connect in AI Providers*, and the flow node's neutral "Connect an account
to run". No entry shows the connect card in place of its destination.

**Keyboard**: no new default shortcuts in 1.0.5. Blender's keymap is crowded, and every binding must also follow
the keyconfig-reload rule. Keyboard routes:

* In Manual, Blender's own Ctrl+PageUp and Ctrl+PageDown cycle through the workspace tabs, Angelo's included.
* F3 search finds **Media Studio** and **Generation Flows**. The operators behind the menus must not be
  ``INTERNAL``; ``toggle_studio`` and ``show_flows`` are ``INTERNAL`` today.
* The Window menu is keyboard-navigable.
* In the Flows popup and the start page, the arrows move, Return opens and Esc closes, with the ``FOCUS`` ring.

4. Visual language
==================

Tokens come from ``ui_tokens`` and ``card_kit`` only. Every size is multiplied by the UI scale. Each new surface
needs captures at scale 1 and at scale 2.

* **Top bar.** Chat, Studio and Flows are one component: ``emboss=True``, the same height, a 16 px icon, the same
  gap, and the pressed state while their home is shown. Today there are three treatments: Chat is pressed, Studio
  embossed, and Flows flat (``flow_open.draw_top_bar`` lacks ``emboss=True``). Order: Chat · Studio · Flows ·
  mode menu. When compact, labels drop and icons stay (``top_bar_fit``).
* **Pills.** The Browser and Shelf header pills, *Turn into a flow* and the Flow bar's *Media Studio* link share
  one style:

  * Height ``BUTTON_HEIGHT_SMALL`` (28), radius height/2, fill ``PANEL``, a 1 px ``LINE`` border.
  * Text in ``TEXT`` at ``TYPE_SMALL`` (12), a 16 px icon with an 8 px gap, 12 px side padding, 6 px between
    pills.
  * States: hover ``PANEL_HOVER``; pressed ``TEAL_SOFT``; focus a 2 px ``FOCUS`` ring.
  * Labels drop to icon-only before anything is ellipsized; the tooltip names the destination.
  * Never teal-filled. Teal fill is for the one primary action per surface (in Media Studio, *Generate*).

* **Asset Browser states.** Centred, with a maximum width of 360 px, wrapped and never ellipsized:

  * The Media Studio glyph at 32 px, then the title at ``TYPE_HEADING`` (16).
  * For no results, one ``MUTED`` line at ``TYPE_BODY`` showing the query.
  * **Generate in Media Studio** is the primary button: ``BUTTON_HEIGHT`` (36), ``TEAL`` fill, ``INK`` text.
  * **Clear search** is a secondary pill placed before it.

* **Popups and start pages.** The Flows popup, the Generation Flows start page and the **+** menu share one
  look:

  * Fill ``CARD``, radius ``RADIUS_PANEL`` (12).
  * The **+** menu's row component: ``ROW_HEIGHT`` 44, with an icon tile, title and why line.
  * The **+** menu's section-header style.
  * Minimum width 320 px; why lines wrap and are never ellipsized.

* **Media Studio palette (1.0.5; maintainer decision of 10-20; owner D).** Media Studio is restyled to the shared
  tokens. Swap the constants in ``studio.py`` exactly as listed, with no layout change:

  .. list-table::
     :header-rows: 1

     * - ``studio.py`` today
       - Becomes
       - Why
     * - ``BG`` (0.047, 0.028, 0.073)
       - ``card_kit.CARD`` (0.090, 0.098, 0.118)
       - Window ground, as the welcome and notice cards
     * - ``PANEL`` (0.081, 0.047, 0.115)
       - ``card_kit.PANEL`` (0.122, 0.133, 0.157)
       - Column panels
     * - ``FIELD`` (0.065, 0.038, 0.096)
       - ``card_kit.CARD``
       - Fields are recessed below their panel
     * - ``LINE`` (0.17, 0.12, 0.23)
       - ``card_kit.LINE`` (0.200, 0.215, 0.245)
       -
     * - ``HOVER`` (0.15, 0.105, 0.225)
       - ``card_kit.PANEL_HOVER`` (0.150, 0.163, 0.192)
       -
     * - ``PURPLE`` (0.33, 0.25, 0.93) as fill: primary button, active tab, progress
       - ``card_kit.TEAL``, with ``INK`` text on it; hover ``TEAL_HOVER``, pressed ``TEAL_PRESSED``
       - White on teal fails contrast; card_kit primaries use ``INK``
     * - ``PURPLE`` as the focus outline
       - ``card_kit.FOCUS``
       - The shared focus ring
     * - ``SELECTED`` (0.16, 0.105, 0.31)
       - ``card_kit.TEAL_SOFT``
       - Chosen chips (1:1, 2K, …) and the active folder
     * - ``TEXT``, ``MUTED``
       - ``card_kit.TEXT``, ``card_kit.MUTED``
       -
     * - ``GREEN``
       - ``ui_tokens.SUCCESS`` (= ``TEAL``)
       - Done states are brand teal
     * - ``ERROR``
       - ``ui_tokens.DANGER`` (= ``CORAL``)
       -

  Every other hard-coded colour in ``studio.py`` and ``studio_*.py`` also resolves to one of these tokens.
  Re-capture the Media Studio window on both tabs, empty and with items, in en and de_DE.

5. Hierarchy: one route per view
================================

Entries fall into three tiers:

* **Primary.** The top bar's Studio and Flows, always visible in both modes. In Manual they and the workspace
  tabs are one control with two faces: pressing either shows the other as active.
* **Secondary.** The Window, Angelo, **+** and Add Workspace menus. Each appears only on demand.
* **Contextual.** The Asset Browser and Shelf pills (permanent there, at the maintainer's request), the
  Browser's empty and no-results states, *Turn into a flow*, the Flow bar's *Media Studio* link, and the welcome slideshow
  card (first run only).

No two always-visible routes may lead to the same place in one view. Applied:

* Media Studio has one Flows route, *Turn into a flow*. A plain "Flows" entry beside it would repeat the top
  bar's Flows, so it is dropped (owner A).
* The Shelf shows only its two header pills. The *Angelo Media Studio* tab only lists assets.
* The Browser's state button says **Generate in Media Studio**, an action, while the header pill says **Media
  Studio**, a place. That repetition is accepted: the state explains why the view is empty, and the header keeps
  the permanent route the maintainer asked for.
* The welcome slideshow names the top-bar buttons, and the welcome is modal, so its Try it pills never share a
  view with them.

6. First run: one clear path
============================

A new user's one path is **welcome ▸ Start creating ▸ connect an account ▸ describe it in the chat**. Media Studio
is the second thing they learn, and Flows the third.

* **The welcome's feature slideshow** (owner F), under the heading **What you can do**:

  * It **replaces** the welcome's Media Studio tile. The *Two ways to work* cards stay above it, so the modes get
    no slide, and nothing is introduced twice in one card.
  * The header, *See it in action* and *Start creating* (the welcome's only teal primary button) stay.
  * It opens on slide 1 every time and moves with its arrows, its dots, the ← / → keys and swipe.
  * There is no autoplay, and Reduce Motion is respected.

  Each slide has a vector picture (card_kit), a headline, one line, and a secondary pill named with its own verb
  (section 4 tokens, never teal). The pill:

  * closes the welcome, then acts through the shared openers, so it goes to the mode's home (section 3);
  * never sends a chat message and never needs an account.

  Slides, in order (start, then go further):

  .. list-table::
     :header-rows: 1
     :widths: 4 22 36 18 20

     * - #
       - Headline
       - Line
       - Pill
       - Tooltip and action
     * - 1
       - Describe it, Angelo builds it
       - Ask in plain words and watch Angelo build it in your Blender scene.
       - **Try an example**
       - "Show the chat with an example request, ready to send". Shows the chat as its top-bar button does and
         puts an example in the composer, unsent. Before a connection, it shows the chat with its connect card.
     * - 2
       - Start in Media Studio
       - Generate images and 3D models in one step.
       - **Open Media Studio**
       - The Studio tooltip (section 1); ``open_media_studio`` AUTO.
     * - 3
       - Go further with Generation Flows
       - Chain generation steps into a flow you can rerun, or turn any Media Studio setup into one.
       - **Open Generation Flows**
       - The Flows tooltip (section 1). In Manual it opens the *Generation Flows* tab; in Director, A's popup
         under the top-bar Flows button.
     * - 4
       - Everything you make, ready to use
       - Your images and models appear as assets in the Asset Browser and Asset Shelf.
       - **Show my assets**
       - "Show the Asset Shelf on its Angelo Media Studio tab". Shows the 3D view's Asset Shelf region on that
         tab. This is the one layout change, and the slide names it.

  Conditions:

  * Slide 3's "or turn any Media Studio setup into one" ships only with A's *Turn into a flow*. Without it, the
    line is "Chain generation steps into a flow you can rerun."
  * Slide 4 ships only with E's library and D's shelf tab.
  * English headlines are at most 34 characters and lines at most 100. Every language wraps; nothing is
    ellipsized.
  * A concept icon in a picture is section 2's icon.

* Two of the chat's six suggestions generate media through the chat. They teach that the chat can do it; Media
  Studio is for doing it by hand.
* The **+** menu's CREATE section lists Media Studio, then Generation Flows, with the why lines from section 0.

7. Checks every owner runs
==========================

* Capture each new surface in English and de_DE, at UI scale 1 and 2, at rest, on hover and open. In Manual, also
  capture the strip with each Angelo tab active.
* The top-bar matrix also covers Arabic (``ar``) at UI scale 1 and 2, in both modes, checking:

  * button order, as in the Right-to-left rule in section 1;
  * icons sitting left of their labels;
  * that each descriptive label reads the catalogue string;
  * that compacting matches English at the same bar width;
  * the shortcut's character order.

* The top-bar matrix runs in both themes, Angelo Dark and Angelo Light: the three buttons keep one treatment in
  each.
* No label or why line is ellipsized in any capture.
* A host test pins:

  * the labels to section 1 and the why lines to section 0;
  * the icons to section 2, through their icon id functions, never a stock icon name;
  * the strip order to section 3;
  * the Media Studio palette to card_kit's tokens (no literal colour in ``studio.py``).
