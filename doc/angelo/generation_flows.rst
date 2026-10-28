.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Generation Flows
================

A **Generation Flow** composes multi-step image and 3D generation as a node
tree: for example a sketch and a style note feed a character sheet with
variants, the chosen variant feeds a turnaround, and the turnaround feeds a 3D
model that can be placed in the scene. Flows use the existing Media Studio
models, accounts, workers and library (see ``generative_studio.rst``); they add
no provider, credential or billing path of their own.

Flows live in Blender's own Node Editor as the **Generation Flow** tree type.
The agent builds and runs them through ``angelo_flow_*`` tools, and users who
want hands-on control open the same tree in a Node Editor. There is no separate
editor or canvas.

Opening flows
-------------

Director never shows an editor-type menu, so a flow is reached directly (1.0.5, ``flow_open``): **Flows** beside
Studio and Chat in Director's top bar, **Generation Flows** in the Angelo menu (both modes), and the group of the
same name in the chat bar's + menu, Angelo's main navigation. Each offers **New Flow**, the templates and the
person's own flows (by name, up to twelve). A flow opens in its own window, a Node Editor showing it, copied from
the largest editor as Media Studio's window is, so no layout is rearranged; a flow window ``flow_open`` opened is
reused for the next flow. Once the window has its real size and every node has been drawn (a node's size is known
only then), the whole graph is framed with a margin (``frame_when_drawn``, Blender's View All), for any flow.
In Manual the flow opens on the *Generation Flows* workspace tab instead (``studio_workspaces``), which shows
the Generation Flows rows (New Flow, the templates, your flows) while no flow is open; the top-bar Flows switches
to it and back. That window and tab show the flow in Angelo's look (``flow_window``): Blender's node editor header (View / Select / Add /
Node, the node-group picker), sidebar and toolbar are hidden, and a bar in the welcome's card language names the flow
with what the hidden sidebar said under it (its step count, "Running" with **Stop** while a run is live, or the
flow's error in amber), links back to **Media Studio** with the shared pill, and offers **Add step** and **Run all**;
mirrored in a right-to-left language. The editor is marked ``Area.angelo_owned`` (runtime only, never saved: the
add-on marks it again after every file load), so its hidden regions get no toggle arrows. Its steps draw in the card language in any editor (``node_draw.cc``
swaps the theme's widget colours and node backdrop for card_kit's tokens while a flow tree draws, and restores
them, so every other tree keeps the person's theme; ``test_blender_flow_node_theme``). A flow shown in a
person's own layout keeps Blender's editor around it.
**Add step** opens Angelo's dropdown (``flow_menu``) of the step types (``flow_window.step_rows``: Inputs, Generate,
More steps, each with a tile and a why line), never Blender's node Add menu; a row adds that step at the pointer for
the person to place, as Shift+A does. Each mode has one home for flows (``studio_entry_points.rst``, section 3):
Manual's **Flows** switches to the Generation Flows tab; Director's **Flows**, the Generation Flows pill and the
welcome's slide (``flow_menu.open_from_top_bar``) open the **Flows popup** under themselves, never Blender's dropdown
or the + menu, and change no layout. It lists the registry's rows, New Flow then *Start from a template* then *Your
flows*, with the + menu's row (``chat_add_menu.draw_row``; a tile only on New Flow), in the popup look (``CARD``,
``RADIUS_PANEL``, ``ROW_HEIGHT``, at least 320 px, why lines wrapped and never cut), inside the editor below the
button, scrolling when short, mirrored right to left. While open it holds the window's input as a menu does
(``input_policy.HOLDING_TOOLS``): a row runs with an Undo step; a click elsewhere, Escape or the button closes it;
arrow keys move a ``FOCUS`` ring and Return opens. Proof: ``test_native_flow_menu`` and ``test_blender_flow_menu``
(pixels: the CARD panel, a tile only where a glyph is, the bar's status line and pill); the start page's whole
why line, in English and Arabic, in both the window and the tab: ``test_blender_flow_start``. A step that only waits for an account or a model says "Connect an account to run" or
"Choose a model to run" neutrally beside Connect in AI Providers; red is for a run that failed or a block the
person's own choices caused. Node text fields never cut their text (``flow_text_field``): one line when it fits at
the node's width, else every line wrapped with Edit opening a wide field.

Media Studio's one route here is **Turn into a flow** (``studio_to_flow``; ``studio_entry_points.rst``, sections 0
and 5), the shared pill at the right end of Studio's top row, which Studio draws (``draw_pill``) and sends to
``turn_into_flow``. It builds an ordinary flow from Studio's setup as it is and opens it through ``flow_open.show``:
Studio's tab picks a Generate Image step, or Generate 3D followed by Place in scene (the Image to 3D template when
there is a reference); the step carries Studio's account and model (the same catalog entry), prompt and the
settings that model accepts (``studio_agent.capabilities``), one output and no review; each reference becomes a
wired Image step (a link stays a link, an attached session file is added to the library first, as Import does; 3D
takes one). Until a step is added or removed the bar says "From Media Studio: add steps after it with Add step"
(``flow_window.hint``). Nothing runs: running is Run all, with its approval. Proof: ``test_native_studio_to_flow``
and ``test_blender_flow_menu`` (the first step's model and prompt read from the node).

The templates are one table, ``flow_templates.TEMPLATES`` (1.0.6 folded 1.0.5's starter flows into it: one list,
never two). Every entry point lists it and creates through ``angelo.flow_create`` with a ``template`` id: the Flows
popup and start page (``flow_open.menu_rows``), the Window and Angelo menus' submenu, the + menu's group and the Node
Editor's **Add ▸ Templates** (which puts the new flow in that editor; anywhere else it opens at the mode's home,
``flow_open.show``). The result is an ordinary ``angelo.flow/1`` flow, the person's to edit, run and save: **Image to
3D** (an Image node, a 3D model, Place in scene), **Image to reference to 3D** (a Reference image step with two
variants to review first) and **Character in parts**, from an image or a description (see "Templates and character
parts"). A generator step names a role, never an account: it is resolved to one of the person's connected accounts,
and a step whose role finds none asks to choose or connect one. A step's stored name is its identity for the agent
and its outputs and stays canonical English, whichever way it was made (the templates, Turn into a flow, and any
added node: its ``init`` replaces Blender's translated ``DATA_`` name; a second reference is ``Reference.001``);
``flow_setup.step_label`` shows a default name (``flow_templates.step_names`` and the node types) in the interface's
language, so changing the language re-labels every flow, and every place that quotes a step (a gate, the bar's
status, Run all's tooltip, an approval, a run card, the Recipe panel) quotes it as shown. Flow names Angelo gives
(New Flow's, a template's title, Turn into a flow's) are stored the same way and shown by ``flow_templates.flow_label``
(``test_native_flow_step_names``). A default prompt stays English. Templates marked ``studio`` also run in Media
Studio's **Workflows** tab as plain input → output (``generative_studio.rst``, "Workflows").

Module ownership
----------------

============================  ===================================================================
Module                        Responsibility
============================  ===================================================================
``flow_spec.py``              ``angelo.flow/1`` definitions, node types, inputs, edits and
                              revisions. No Blender dependency.
``flow_tool_spec.py``         The ``angelo_flow_*`` tools as the model sees them: schemas,
                              descriptions, call checks and ``declared_form``.
``flow_graph.py``             Input keys, step states, run plans and card selections. Pure.
``flow_runner.py``            One approved run, advanced a tick at a time through a host adapter.
``flow_prices.py``            Dated per-generation cost estimates with their sources.
``flow_subflows.py``          Subflow expansion: recipe steps as ``<subflow>/<step>``. Pure.
``flow_recipe_library.py``    Saved recipes outside any .blend. No Blender dependency.
``flow_nodes.py``             The node tree, sockets and nodes, including the Subflow node;
                              ``flow_character_nodes.py`` adds the character-parts nodes.
``flow_tree.py``              Tree <-> definition conversion and Subflow socket sync.
``flow_steps.py``             Where a step's settings and per-use state live; recipe lookups.
``flow_host.py``              Main-thread runtime: evaluation cache, previews, runs and the
                              Studio adapter; ``flow_capture.py`` runs Scene Capture steps.
``flow_templates.py``         Bundled templates (``angelo.flow-template/1``) and role
                              resolution. No Blender dependency.
``flow_parts.py``             Parts-sheet split geometry (numpy). ``flow_split_host.py`` runs
                              it off the main thread and imports the crops.
``flow_fanout.py``            Per-part expansion of ``GENERATE_3D_EACH``. Pure.
``character_layout.py``       Place Character's placement math (numpy); ``character_compose.py``
                              builds the scene and ``flow_character_ui.py`` adds its operator.
``flow_open.py``              Every entry point that opens or creates a flow: the flow window,
                              ``angelo.flow_create``, the Flows and Add ▸ Templates menus and the
                              + menu's Generation Flows group.
``flow_ui.py``                Node Editor operators, the manual run dialog and the sidebar;
                              ``flow_recipe_ui.py`` adds the Recipe panel and its operators.
``flow_open.py``              Opening flows: each mode's home, New Flow, templates, the menu rows.
``flow_window.py``            The flow bar (name, status, Media Studio, Add step, Run all) and
                              the hidden chrome; the areas it owns are re-marked on file load.
``flow_start.py``             The start page while no flow is open: every why line, the rest counted.
``flow_menu.py``              Angelo's dropdown: Director's Flows popup and Add step's steps.
``studio_to_flow.py``         Media Studio's Turn into a flow: its setup as a flow's first step.
``flow_tools.py``             The agent tools on the existing controller/executor path.
``flow_approvals.py``         Approval text; ``flow_chat.py`` draws the chat cards.
============================  ===================================================================

Definitions
-----------

The node tree saved in the .blend is the source of truth. ``tree_flow`` reads
it as a canonical ``angelo.flow/1`` definition and ``write_flow`` applies an
edit back, leaving inputs whose links already match untouched so the user's
reroutes and frames survive. Both the Node Editor and the agent edit the same
definition.

Node types (``flow_spec.NODE_TYPES``):

* Sources: ``IMAGE`` (a library image asset or a public HTTPS URL),
  ``SCENE_CAPTURE`` (the scene camera or current view, clay or material
  preview, whole scene or selection) and ``TEXT`` (a prompt fragment).
* Generators: ``GENERATE_IMAGE`` and ``GENERATE_3D``: an exact catalog account
  and model, a prompt, Studio options, 1-4 variants and a review flag.
* ``PLACE_IN_SCENE``: imports the chosen 3D output, with Undo.
* ``SUBFLOW``: runs a recipe flow as one step (see "Recipes and subflows").
* Character parts: ``SPLIT_SHEET``, ``GENERATE_3D_EACH`` and ``PLACE_CHARACTER``
  (see "Templates and character parts").

Inputs and their limits are ``flow_spec.INPUTS``: prompts take up to eight
Text links in order; image references take up to four (one for 3D), and the
model's own catalog limit is checked when the step is evaluated. References
are always links: "Pick image..." on a node imports the image into the library
if needed and adds an Image node wired into the input. Wrong-kind links,
links past an input's limit and links that would close a cycle are shown in
red and left out of the definition; they never invalidate the whole tree.

Node names are the step identities the agent uses. An incomplete node (no
account, no image picked) is allowed while editing; evaluation reports it as
``BLOCKED`` with the reason.

A generator without a usable account says so in the interface, as Media Studio
does. ``flow_graph.connection_needs`` names what each generator step lacks:
``connect`` (no connected generation account of its kind at all),
``reconnect`` (its chosen account isn't connected) or ``choose`` (one is
connected, none chosen). ``flow_host`` prepares it with the states, so drawing
reads only that. A ``connect`` or ``reconnect`` step draws, under its model
picker, *No image generation account yet*, *No 3D generation account yet* or
*This step's account isn't connected* with **Connect in AI Providers**
(``angelo.flow_connect``, whose tooltip says it opens AI Providers to connect
an image or 3D generation account); a Subflow use draws it when one of its recipe's steps
needs it, and the Flow sidebar says *Connect a provider to generate* with the
same button. A run attempt with nothing to generate whose blocked steps need
an account shows a popup naming them, with the same button, instead of only a
status-bar line; one that can still generate lists them in its dialog as left
out. A ``choose`` step only asks for a model (the picker reads *Choose a model*
until one is picked). The steps' model-facing reasons
(``choose an account and model``, ``connect or check this account in AI
Providers``) are unchanged.

Input keys, reuse and staleness
-------------------------------

Each generator step's **input key** hashes its account, model, options,
composed prompt (its own prompt, then linked Text fragments in link order) and
the exact assets it reads. Every output records its lineage in the Studio
library record: ``{source: flow, flow_id, flow, node, key, variant,
reference_ids}``, and the asset is filed in the Studio folder "Flow · <flow name>"
(created on first use) so it can be found in the library. A step whose key has
enough ready outputs is reused for free;
editing anything upstream changes the key, and the step shows ``STALE`` (it has
outputs for an older key, recorded in the node's ``last_key``) or ``MISSING``.
Nothing reruns on its own.

States: ``READY``, ``PARTIAL`` (some variants), ``MISSING``, ``STALE``,
``WAITING`` (an upstream step has no output yet), ``RUNNING``, ``FAILED`` (the
last attempt for this key failed) and ``BLOCKED``.

Each generator keeps a **chosen output**; it defaults to its first output and
is what downstream steps read. Choosing another output changes downstream keys.

Scene captures render with the ``angelo_view`` capture path when a run is
planned, then become imported library images, so generators reference real
pixels. A capture is reused while no other scene update has happened (flow node
edits don't count) and for at most 15 minutes. Planning first evaluates pending
edits, so an edit in the same tick invalidates it. Identical pixels keep the
previous asset, so unchanged scenes keep downstream steps reusable.

Runs and approval
-----------------

``flow_graph.plan`` lists every generator in scope (``ALL``, ``NODE``,
``DOWNSTREAM`` or ``UPSTREAM``) with ``GENERATE``, ``REUSE``, ``RUNNING`` or
``BLOCKED``. A step generates when it is missing outputs, or when an upstream
step in the same plan generates (then all its variants are new). Each
generating line carries an ``approval`` record: the exact request, except that
references produced by this run are named ``["run", node]``.

``flow_graph.select`` applies the user's card choices. **Unticking** a step
leaves it and every planned step after it out. A **review stop** on a
generating step (on by default when it makes variants) defers the planned steps
after it to a later card. At most 16 paid generations run per approval.

One approval covers one run, and it lists every paid generation: step, count,
account, settings and estimated cost, plus reused and blocked steps. This
replaces the per-generation Studio card for flows only; a single Studio
generation keeps its own card. Read-only chats can't run flows.

* **Agent runs** (``angelo_flow_run``): the precondition plans the run (capturing
  scene inputs) and becomes a ``FlowReview``, which compares by plan digest. The
  chat draws an interactive card from it; clicks change the pending call's
  ``flow_selection``. The controller re-checks the precondition after approval
  as for any tool, so an edit while the card is open refuses the run.
* **Manual runs** (Node Editor "Run this step", "From here", sidebar "Run flow"):
  a dialog lists the same lines with checkboxes and review-stop toggles;
  confirming it is the consent, like Studio's Generate button. The plan is
  rebuilt on confirm and refused if it changed while the dialog was open.

``flow_runner.FlowRun`` then owns exactly the approved lines. Before starting a
step it rebuilds the request from the current flow and library and refuses
anything that differs from the approval, except that ``["run", node]``
references must equal that step's chosen output from this run. Variants of a
step start together through ``StudioJobs`` (four concurrent jobs, shared with
Studio). A failed step skips the steps that need it; independent branches
continue; partial variant success counts as done. Nothing is retried. Stop ends
local monitoring only; submitted provider work may still finish and be billed.
The live chat card reports Running while any approved step is queued or active.
Done, Partly done and Failed describe terminal outcomes, not an unfinished run.

The agent tool's result is deferred like a Studio generation (``FlowPending``)
and arrives at the end of the run or at a review stop, with
``review_stop: {node, pick_from, remaining_steps}``. Its call expiry is extended
to 300 s plus 1,300 s per dependent step level, at most four hours. A run's
progress is not saved: after a restart or file load the run is gone, its
started generations remain Studio assets, and the flow shows their steps from
the library. Nothing is resubmitted.

Recipes and subflows
--------------------

A **recipe** is an ordinary flow with an interface, ``recipe: {inputs,
output}``: ``inputs`` are some of its Text, Image or Scene Capture nodes and
``output`` is one of its generators. In the tree, the output is
``angelo_recipe_output`` and each input is a source node with "Recipe input"
(``exposed``) on. An incomplete interface reads as a plain flow, and the
Recipe panel says why. A recipe can't contain a Subflow node, which also
rules out recipe cycles.

A ``SUBFLOW`` node names a recipe in the same file (a ``PointerProperty``, so
the recipe is saved with the file and its users are counted). Its inputs are
the recipe's input names, each taking one link of that input's kind. Its output
is the kind of the recipe's output step. ``flow_tree.sync_sockets`` keeps the
sockets in step with the recipe. The pump resyncs every use when a recipe
changes, and links to a removed socket go with it.

Before evaluation, ``flow_subflows.expand`` replaces each Subflow node with
copies of the recipe's nodes named ``<subflow>/<step>``. A link into an input
replaces that exposed source node; an unlinked input keeps the recipe's node
as its default. Links from the Subflow leave from the copy of the output step.
Evaluation, planning, approval cards, review stops and runs then work on the
expanded flow unchanged, so there is still one run path. Input keys don't
include node names, so two uses with identical inputs, or the recipe run on
its own with those inputs, share outputs for free. A Subflow that can't be
expanded (no recipe, a missing recipe, an inner name over 63 characters or
one that collides with another node) stays one ``BLOCKED`` node with the
reason; only the steps after it wait.

Each use keeps its own state for the recipe's steps on the Subflow node: chosen
outputs (in the definition as ``chosen: {step: asset}``), last input keys and
scene captures. The shared recipe tree is never written by a run of a use.
``flow_steps.step_slot`` resolves an expanded step name to its settings node
(the recipe's) and its state holder (the Subflow node).

Scopes name a Subflow by its node name: ``NODE`` runs all of its steps,
``DOWNSTREAM`` and ``UPSTREAM`` add what follows or precedes any of them.
Inner steps appear on cards as "<subflow> › <step>". ``angelo_flow_choose``
takes the expanded name. ``angelo_flow_inspect`` shows the outer definition
(nodes, links and revision, which is what ``angelo_flow_build`` edits), with
each Subflow's expanded ``steps`` and states. Without a flow it lists recipe
interfaces and saved recipes.

**The recipe library.** "Save to Recipe Library" writes the recipe, without
chosen outputs, to ``<Studio library>/flow-recipes`` as one JSON file per name
(``angelo.flow-recipe/1``, at most 200). Recipes name library images as
defaults, so they live with that library. Adding a saved recipe ("Add recipe
as Subflow…", or ``angelo_flow_build`` naming one in a ``SUBFLOW`` node) first
copies it into the file as a recipe flow. From then on the file's copy is what
runs, so a .blend never depends on the library. The build result lists
``recipes_copied_into_file``.

Prompt cost (toolset 26, which added the ``SUBFLOW`` node and recipe
interfaces to the ``angelo_flow_*`` schemas): the flow family loads on demand,
so a Claude turn and an OpenAI Codex turn are unchanged, and a session's first
flow lookup is about 210 tokens larger. Voice threads and function-calling
connections declare every tool on every turn (see "Agent tools").

Templates and character parts
-----------------------------

A **template** (``flow_templates``, ``angelo.flow-template/1``) is a bundled,
versioned flow, created as a new tree of ordinary, editable nodes from any entry
point in "Opening flows". A template marked ``studio`` (the two Character in parts
templates) also runs in Media Studio's **Workflows** tab as plain input → output
(``generative_studio.rst``, "Workflows"): the same instantiated flow, planner,
approval card and runner, so a step finished in one surface is reused in the other.
The chat bar's **+** ▸ **Workflows** opens Media Studio on that tab
(``angelo.open_media_studio`` WORKFLOW: its window in Director, its workspace tab in
Manual; ``studio_entry_points.rst``). A generator node names a *role* (the kind of model
it needs and the models to prefer), never an account: ``instantiate`` resolves
each role to one of the person's ready catalog entries, preferring the listed
models, and applies that provider's options. An unresolved role leaves the step
asking for an account, as any step does. Titles and summaries are marked with
``n_`` and translated where drawn; prompts are model input and stay English.

**Character in parts** (and its from-a-description variant) is Character image
→ *Parts sheet* (an image edit of the character, with a review stop) → *Split*
→ *Parts 3D* → *Character*. The sheet prefers GPT Image 2.5 Sunburst, then
Flare, at 3840 × 1920; the parts prefer Meshy 7, then 7.1.

* ``SPLIT_SHEET`` is free and local. The sheet is a grid of slot columns (front
  views, and back views when ``rows`` is 2). Each slot has a kind (exactly one
  ``BODY``). A slot with ``items: split`` gives each separate item its own
  numbered part, up to six: islands whose pixels come within 1% of the cell's
  height of each other are one item (nearness between pixels, never boxes: a bag
  whose strap reaches up beside a scarf is its own item), a piece enclosed by
  another item (a pendant inside its chain) stays with it, a thin line spanning the
  cell along its edge is a separator the inset missed and is dropped, and each
  item's crop keeps only its own pixels. A split is keyed by the sheet asset and layout
  (``flow_graph.split_key``) and runs once: Blender decodes the sheet on the
  main thread, a worker cuts transparent crops (``flow_parts``, about 0.35 s for
  3840 × 1920), and the next pump imports them with lineage ``source: split``.
  The split is stored on the node, but its crops are content-addressed by split
  key, part and view: splitting the same key again (after Undo, a node that lost
  its stored split, or the same sheet in a new flow) reuses those crop assets
  (``flow_split_host.existing_crop``, one index per collect; a crop whose file is
  gone is cut again), so every part step keeps its input key and models already
  made stay reused. The split key includes ``flow_graph.SPLIT_ALGORITHM``, pinned
  to ``flow_parts``' thresholds by a host test, so crops cut by earlier rules are
  never reused by key after the rules change; a crop of the same sheet, part and
  view whose file has the same bytes as the new cut is still reused, so only the
  parts whose pixels changed need new models.
  A sheet with no body is ``BLOCKED`` with the reason.
* ``GENERATE_3D_EACH`` expands, once its split is ready, into one
  ``GENERATE_3D`` step per part named ``<node>/<part>`` (``flow_fanout``). Each
  has its own approval line, input key, chosen output and retry; variants are 1
  and there is no review stop. The node keeps the per-part choices. In every run
  scope the node's name stands for its part steps (``flow_graph.scope_nodes``,
  as a Subflow's name does), so running the node runs its parts.
* ``PLACE_CHARACTER`` is ready once the body's model is chosen; **Place
  character** finds every chosen model first and removes what it added if any part
  fails, so a refused Place changes nothing; otherwise it adds every chosen part in
  one Undo step, as separate objects under
  one root empty at the 3D cursor. The body is scaled to ``height``; each part is
  placed in its box on the character image when the split carries a fit
  (``grounding``, stored by Media Studio's **Fit parts to my image**), else in
  its sheet box, with clothing widened or narrowed to match the generated body
  (the body profile always comes from the sheet). Clothing is pushed just outside the body (Shrinkwrap,
  outside only), and what a layer covers is hidden with a Mask modifier
  (vertex group ``angelo_covered``), never deleted.

A parts sheet draws each part at its own scale, not in one frame: against
hand-labelled boxes on the ten research characters, garments come out about
1.5x and small parts (hats, hair, held items) about 2.4x too large, and on the
six held-out characters sheet boxes put parts a median 20.6 cm off (90th
percentile 45 cm) on a 1.7 m character. Boxes found on the character image
(``parts_ground``) were 1.3 cm off (90th percentile 3.7 cm) with GPT 6.1 Sol, so
a fit replaces the sheet box for every part it finds. Registering parts to a
whole-character 3D model made 3 of 3 worse and was dropped (kept on
``experiment/character-parts-registration``).

Prompt cost: no turn grows. Voice threads and function-calling connections,
which declare every tool on every turn, get ``angelo_flow_build`` in its
declared form (``flow_tool_spec.declared_form``), byte-identical to before;
where flow tools load on demand, its full form names the three node types,
503 bytes more per flow lookup.

Cost estimates
--------------

``flow_prices.PRICES`` maps exact (provider mode, model) pairs to estimate
functions with a source URL, dated ``AS_OF``. Providers don't report what a
request cost, so cards say "≈" with the date, an unknown price is shown as
unknown and makes the total "at least", and the module docstring lists every
assumption (OpenAI ``auto`` quality estimated as ``high``, and the per-credit
price assumed for Tripo and Meshy). The enforced limit of an approval is its
list of generations, not the estimate.

Agent tools
-----------

All four are a deferred family (``plan_kinds.DEFERRED_TOOLS``): a Claude plan
names them in its generic variant and reads their exact schemas and
``agent_guidance.FLOW_GUIDANCE`` on demand. OpenAI Codex threads load them on
demand from the ``more`` namespace (``tool_specs.codex_tools``); voice threads
and function-calling connections declare them on every turn, in their declared
form (``flow_tool_spec.declared_form``). Toolset 20 added them.

* ``angelo_flow_inspect``: list flows, or one flow's nodes, links, states,
  outputs (asset IDs), revision and cost estimate. Read-only.
* ``angelo_flow_build``: create or edit by node name; ``expected_revision`` is
  null to create, else the current 12-character revision. A bounded, undoable
  document edit with the usual approval mode; nothing is generated.
* ``angelo_flow_run``: paid usage behind the itemized card; never auto-approved.
* ``angelo_flow_choose``: set a step's chosen output (one of its current
  outputs). A bounded, undoable edit.

Results carry node names, states and asset IDs, never file paths.

Chat cards
----------

``flow_chat`` computes card layout as pure drawing operations and hit
rectangles (host-tested), then draws them with the chat renderer's primitives.
The approval card shows input thumbnails, one row per step with a checkbox and
a review-stop toggle, the estimate and the billing warning; its Run button is
disabled when nothing is selected. The run card, inside the run's activity row,
shows each step's state and output thumbnails: clicking a thumbnail chooses
that output (only while it is a current output of that step), and its **+**
adds the image to the message being written as an attachment. Thumbnails reuse
Studio's bounded texture cache; node previews use a bounded
``bpy.utils.previews`` collection prepared on the flow timer, never in draw.

Validation
----------

* Host: ``tests/python/angelo_agent/test_native_flows.py`` (definitions,
  edits, planning, selection, runs with a fake host, prices, card geometry).
* Host: ``test_native_flow_subflows.py`` (interfaces, expansion, per-use
  choices, shared keys, scopes, review stops and runs through recipes).
* Host: ``test_native_flow_parts.py`` (sheet grids, alpha crops, holes, item
  splits), ``test_native_flow_character_parts.py`` (split states, per-part
  expansion and runs with a fake host), ``test_native_flow_templates.py``
  (instantiation, role resolution, the declared form) and
  ``test_native_character_layout.py`` (placement math).
* Host: ``test_native_flow_connect.py`` (each step's connection need, a
  Subflow use's, the run attempt's steps, unchanged model-facing reasons, and
  the node, sidebar and run-attempt wiring).
* Native GUI: ``test_blender_generation_connect.py`` with no generation account:
  the nodes, a Subflow use, the sidebar and a run attempt point to AI
  Providers; the recipe's own nodes too; once an image key is connected (an
  in-memory fixture, never called) the image step only asks for a model. It
  also covers Media Studio (``generative_studio.rst``).
* Native GUI: ``test_blender_flows.py`` builds a flow through the agent tool,
  captures the scene, renders both cards offscreen, runs two review-stopped
  rounds against an in-process fixture transport (no network or keys), checks
  lineage, choice binding, reuse, staleness, refusal of edits after approval,
  Pick image wiring, red links and Place in Scene, then screenshots the Node
  Editor. Inspect ``flows-qa.json`` and the PNGs; exit status alone is not proof.

* Native GUI: ``test_blender_flow_recipes.py`` builds a recipe and three
  uses through the agent tools. It checks socket sync and inspect output, then
  runs one use through a review stop. It also checks per-use choice and keys
  (the recipe is never written), free reuse by an identical use, copy-in from
  the recipe library, and staleness and socket changes after recipe edits.
  Both Node Editor views are screenshotted. Inspect ``flow-recipes-qa.json``
  and the PNGs.

* Live, billed (opt-in): ``live_flow_smoke.py`` runs one real flow with the
  user's own provider keys: two reference images and a shared negative-prompt
  Text node feed a third image, which feeds a Tripo model that is placed in the
  scene. It checks lineage, library folders, node previews and the run card,
  and never retries. It refuses to run without ``ANGELO_QA_LIVE_GENAI=1``,
  ``--authorize-billed-generation`` and explicit profile, state and library
  directories; the script's docstring gives the command line.

Not supported: subflows inside recipes, more than one output per recipe,
sharing recipes through the team library, video/audio/upscale steps,
team-library and world-reference sources, and resuming a run after restart.
