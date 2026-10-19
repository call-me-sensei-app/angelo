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

Module ownership
----------------

============================  ===================================================================
Module                        Responsibility
============================  ===================================================================
``flow_spec.py``              ``angelo.flow/1`` definitions, node types, inputs, edits, revisions
                              and the tool schemas. No Blender dependency.
``flow_graph.py``             Input keys, step states, run plans and card selections. Pure.
``flow_runner.py``            One approved run, advanced a tick at a time through a host adapter.
``flow_prices.py``            Dated per-generation cost estimates with their sources.
``flow_subflows.py``          Subflow expansion: recipe steps as ``<subflow>/<step>``. Pure.
``flow_recipe_library.py``    Saved recipes outside any .blend. No Blender dependency.
``flow_nodes.py``             The node tree, sockets and nodes, including the Subflow node.
``flow_tree.py``              Tree <-> definition conversion and Subflow socket sync.
``flow_steps.py``             Where a step's settings and per-use state live; recipe lookups.
``flow_host.py``              Main-thread runtime: evaluation cache, previews, scene captures,
                              runs and the Studio adapter.
``flow_ui.py``                Node Editor operators, the manual run dialog and the sidebar;
                              ``flow_recipe_ui.py`` adds the Recipe panel and its operators.
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
interfaces to the ``angelo_flow_*`` schemas): the flow family is deferred, so a
Claude turn is unchanged. A session's first flow lookup is about 210 tokens
larger, and so is every Codex turn, since Codex receives the flow tools with
its complete tools (see "Agent tools").

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
``agent_guidance.FLOW_GUIDANCE`` on demand. Codex receives them with its
complete tools. Toolset 20 added them.

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
