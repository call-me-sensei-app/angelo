.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
.. SPDX-License-Identifier: GPL-2.0-or-later

##########
Learn mode
##########

Learn mode shows how a person does by hand what Angelo just did. Its first part is the **how-to card**: for a step
Angelo made with typed operations, the command a person would use, its keys in their own keymap, where it is in the
menus, the settings Angelo used and what must be true first. This document is the contract of the card: its data,
where it shows in the transcript, its drawing, the per-chat option and the + menu entry.

.. contents::
   :local:

What a card is built from
=========================

A card is built from the step's saved row (``activity_rows``: its ``tool`` and ``arguments``), when the row is
opened. It is display data: never stored, never sent to a model, and nothing in these modules is read by one. Every
typed step of every saved chat therefore has a card, and with no row open no learn code runs.

.. list-table::
   :header-rows: 1
   :widths: 24 76

   * - Module
     - Owns
   * - ``learn_routes.py``
     - The reviewed hand routes: for each typed operation kind, the command a person uses, **by identifier only**
       (an operator's idname, menu classes, RNA names). No Blender.
   * - ``learn_menu_paths.py``
     - GENERATED from Blender's menu sources by ``tests/python/angelo_agent/learn_menu_paths_source.py --write``:
       where each allow-listed operator sits in its editor's header menus, with the menu's own text and the
       properties the entry sets. No Blender.
   * - ``learn_cards.py``
     - ``steps(message, lookup)``: a row's card as ``Step`` records, each text ready to draw. No Blender.
   * - ``learn_rows.py``
     - Where a card shows: under a typed step that happened (its chip reads Done), when the person opened the step
       or the chat's option keeps the latest request's steps open. ``chat_rows`` calls ``under`` and ``draw_under``.
   * - ``learn_card_draw.py``
     - The card's layout (no Blender: ``layout`` takes the canvas's ``measure`` and ``wrap``) and its drawing with
       the chat canvas's own primitives and ``ui_tokens`` roles, so mirroring, scale and both themes are the
       canvas's.
   * - ``learn_resolver.py``
     - ``Lookup``: what the running app calls a command, where it is and which keys are bound to it now.
       ``card(message)`` builds a row's card with it and keeps recent ones briefly.

Where a card shows
==================

**Only while Teach me as you work is on for the chat.** Learn is strictly opt-in: with the option off (the default)
no card shows anywhere, and an opened step shows exactly what it showed before learn existed. ``chat_blocks.build``
reads the option once per draw of the transcript (``learn_rows.begin``, memory only); while it is off nothing else of
learn runs in the draw: no ``has_card``, no resolver, no layout (pinned by ``test_native_learn_rows`` and, in the app,
by ``test_blender_learn_cards``). Learn has no timer or handler, nothing on the request path, and nothing a model reads.

With it on, a card shows under the step's row, above its technical details, inside the row's own block. A step has a
card when it is a typed edit that **happened**: a planned, declined, failed, previewed or reverted step did nothing to
learn from, and a scripted step is its script. The steps of the chat's latest request are open by themselves and kept
out of a folded work group (``_attached``); an earlier request's step shows its card when the person opens it.

A card reads top to bottom, one block per hand command: its name and its keys as key caps (or "No shortcut in your
keymap"); the key that opens one of its menus; where it is, as breadcrumbs; the settings Angelo used; what must be
true first, as quiet pills; how doing it by hand differs, or why there is no single hand command. No text is cut:
lines wrap and key caps, breadcrumbs, settings and pills flow onto further lines.

``learn_rows.opened()`` counts the steps whose card the person opened themselves and saw drawn this session, and
``learn_rows.kept_open()`` those the chat's option kept open and drew; a lesson's own checks read them, nothing else
does.

A card that fails to build never stops its row: the failure is noted (``chat_guard``) and the row draws without it.

Teach me as you work
====================

A per-chat choice of the person's (``learn_option``, ``learn_ui``), off by default. With it on, the how-to cards of
that chat's latest request are open by themselves and an earlier step's card shows when it is opened; with it off, no
card shows anywhere. The + menu's Learn link and the Learn card's switch are its only entry points.

* **Where**: chat bar **+** > **Learn**, a link in the menu's footer beside Report a bug (registered in
  ``chat_destinations`` by ``learn_ui``; a tile of its own would push the menu's last row out of view at laptop sizes
  in every language, which ``test_native_chat_add_layout`` refuses). It opens the Learn card (``card_host``): what
  learning with Angelo is, and the switch for the chat on screen. Also operator search (``angelo.learn``). The switch
  runs ``angelo.learn_teach`` with the chat's id, so it acts on the chat the card was opened for.
* **More rows**: another module adds rows under the switch with ``learn_ui.add_rows(provider)`` (the lessons do); a
  click closes the card and runs the row's operator.
* **Kept** in ``learn.json`` in the state folder (``{"version": 1, "teach": [chat id, ...]}``, at most 512 chats,
  oldest dropped first), read once at register and answered from memory afterwards, so the transcript's draw does no
  disk work. It is deliberately **not** one of a chat's options (``chat_settings.stored_options``): those travel to
  workers and into cache keys, and this is a view setting. A build that does not know the file ignores it.
* **No model reads or changes it.** It is not in the turn context, a tool or an instruction. The agent learns that the
  feature exists only from its ``feature_guide`` entry, when it looks features up.

Never a guessed label or key
============================

No label, menu title or key is written by a person anywhere in these modules. ``learn_routes`` holds identifiers;
``learn_resolver.Lookup`` reads the text from the app as it is at that moment:

* a command's name and its settings' names from the operator's RNA, translated by Blender;
* a menu's title from its class;
* a key from the person's key configuration (``keyconfigs.user``), printed as Blender's menus print it
  (``KeyMapItem.to_string``).

So a card follows a rebound key, the Industry Compatible keymap, the interface language and the next Blender base
without an edit. Whatever the app does not have is answered empty, and ``learn_cards`` then leaves that part out:
a path with a missing menu is dropped whole, a command with no key shows none, and a command this build does not
have becomes a step that says Angelo's own tool did it. A card never suggests search: search finds only what the
header's menus hold, and a command there has its path on the card already.

Every kind is answered
======================

``learn_routes.hand(operation)`` returns the routes a person takes (one command each, in order) or the reason there
is no single hand command (``NO_ROUTE``). All typed kinds (``action_schemas.ACTION_SCHEMAS``) are answered one way or
the other. ``operator_run`` needs no entry: its record names the operator, and its mode is the kernel's own rule
(``action_operator_spec.edit_only``, ``POSE_MODULES``). Its path is the header's (``learn_menu_paths``): an
operator's label is often not what its menu says (Multi Select Edge Loops is Select › Select Loops › Edge Loops), and
Blender's search (F3) finds a command only through the editor's header menus, under their text
(``interface_template_search_menu.cc``; context menus are left out). The table holds every plain ``operator`` entry
those menus draw, down through submenus shown under their own title and items drawn in place (``menu_contents``); an
entry that sets a property is this action only when the record holds that value (Rotate Edge CW or CCW), and the one
matching the most of the record wins. An entry whose text or properties are worked out at run time, or whose text is
translated in a context of its own, is left out, so a command with no such entry shows no path.

A route's last breadcrumb is the menu's own text for the command (``Route.item``: Bevel Edges, where the command is
called Bevel), copied from Blender's menu source and translated by Blender when shown; a host test holds each one to
that source. Where the menu lists an operator's choices as its entries (the Apply menu, Set Parent To), the breadcrumb
is the choice; where it names a submenu of them first (Set Origin, Separate), both (``Route.submenu``). A route with
no ``item`` ends with the command's own name, so it is only for a menu that lists the command plainly. Where no entry
does exactly what the step did (Apply with Location and Scale), the breadcrumb is the entry for the first, and the
card lists the settings to tick in Adjust Last Operation. A mode no entry performs (deleting faces but keeping the
region's boundary) has no route: it is Angelo's own tool, never the nearest entry.

A route says where the hand tool differs from what Angelo did (``Route.differs``). Angelo works by number and index;
a person works by mouse and click. A card that hid that would teach a route nobody uses.

Keys
====

A key is one the person can press in the editor and mode the step belongs to. ``learn_resolver.KEYMAPS`` names the
keymaps that apply there, most specific first, followed by the window's. An item counts when it is active, is a
keyboard item, and **every property it sets equals what the step used, or the command's default where the step named
none**: Ctrl B is the edge bevel, and Ctrl Shift B, which sets ``affect`` to vertices, is not; the key for vertex
select mode is 1, and Shift 1, which also turns ``use_extend`` on, is another action and is not shown. A property the
key leaves alone takes the command's default, so where the step's value is not the default the key must set it, unless
the card leaves that property to the person (``free``): the route's choice, which the key's own menu asks for (Alt P
asks which parent clear), or a setting the card lists, set afterwards in Adjust Last Operation. An ``operator_run``
step's own recorded properties are its route's properties and its listed settings, so one operator bound to several
keys gives each action its own (the vertex, edge and face select modes).
A key that opens one of the path's menus (Shift A for Add,
Ctrl A for Apply) is shown with that menu's title (``Step.opens``).

Cost
====

Nothing runs until a row is opened. ``learn_resolver.card`` keeps the last ``MAX_CARDS`` (32) cards for ``FRESH_S``
(2 s) each, keyed by the row, the interface language and the active key configuration, so a transcript that redraws
an open card every frame looks nothing up again, and a rebinding shows within two seconds. Measured in the app by
``test_blender_learn_routes`` (a two-command card, Apple M4 Pro, 1.1 development build): 4.3 ms to build cold,
0.0012 ms kept.

Validation
==========

* ``test_native_learn_routes`` (host): every typed kind is answered and nothing else is; every route's menus exist
  in Blender's own menu sources (``scripts/startup/bl_ui``), each holds the next, the last holds the operator and
  the first is one the editor's header (or, for a modifier, its panel) shows; a route's ``item`` is the text that
  menu's source gives the command; the Add Modifier pages equal the source's; every allow-listed operator gets the
  kernel's mode and its header path, an entry that sets a property only with the record's value; a card shows only
  what its lookup reported and survives records another build wrote.
* ``test_native_learn_menu_paths`` (host): the checked-in ``learn_menu_paths`` is what this checkout's menu sources
  build, the header menus it starts from exist, and the builder's rules hold on menu sources written for the test
  (titles, context menus, run-time text and values, properties on a call, a name or in a branch, items in place).
* ``test_native_learn_option`` (host): the choice is saved and read back by a fresh start; a damaged file is "off
  everywhere" and is rewritten whole; the list is bounded; a failed write raises, changes nothing and leaves no
  temporary file; asking reads no file.
* ``test_blender_learn_cards`` (in the app, real clicks): a closed step draws no card; a click opens it with the
  command's name as the app gives it, a key cap, the mode and the settings, inside the chat; the + menu's Learn
  link is in view, a click opens the Learn card and its switch turns the option on for the chat and saves it; the latest request's steps then open by
  themselves while an earlier one stays closed and a scripted one has none; Japanese and Arabic; off again.
* ``test_blender_learn_routes`` (in the app, real default startup, no simulated input): every route's operator,
  menus, choice and mapped settings exist in the running build and have labels; for the commands a person uses
  most, the keys shown are bound in the default key configuration and each is a key Blender's own lookup
  (``KeyConfigurations.find_item_from_operator``, what its menus print) reports for that command in that mode; a
  key opens the Add, Apply, Merge and Delete menus; a rebinding shows at once in a new lookup and in a kept card
  once stale; the Industry Compatible key configuration gives its own keys; labels follow the interface language;
  a key that also turns something else on is not shown; one card's cost, cold and kept; a command Angelo ran by
  name ends its path with the header menu's own text, translated, not its label.

Not built yet
=============

Paths for entries whose text is worked out at run time or translated in a context of its own; the
panel a stored setting is drawn in; the labels of settings changed without a command (a modifier's, a material's);
an operator-by-operator account of scripted steps.
