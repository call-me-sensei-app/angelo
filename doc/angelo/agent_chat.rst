.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Native Agent Chat
*****************

The chat is the Coding Agent editor of ``scripts/addons_core/angelo_agent``: a
native GPU/font canvas on a Console editor subtype whose messages are never
Python input. The controller runs the official runtimes (Codex app-server and
unmodified Claude Code) as separate processes; the chat has no browser UI,
embedded terminal, shell, MCP server or computer-use tool (external AI apps reach
Angelo through its own MCP launcher instead, outside the chat: ``external_mcp.rst``). See
``agent_integration.rst`` for the architecture and ``native_editor_build.rst``
for the editor identity, input buffers and build.

Launching a scratch session
===========================

Build the development app (``native_editor_build.rst``), then, from the
repository root::

   ANGELO_STATE_DIR=/tmp/angelo-chat-state \
     ../build-angelo-native/bin/Angelo.app/Contents/MacOS/Angelo \
     --factory-startup --python scripts/angelo/launch.py

``launch.py`` enables the add-on for this session only and opens the chat.
``ANGELO_STATE_DIR`` isolates chats, snapshots, attachments, task memory and the
default file workspace, not accounts; automated fixtures also set
``ANGELO_CONNECTIONS_DIR`` so they never change the person's connections.

Editor and conversations
========================

* The editor picker lists Coding Agent under its own AI heading. Python Console
  stays in Scripting, Shift+F4 always opens Python, and Python history and chat
  drafts are separate buffers.
* Angelo starts in Director Mode with the chat beside a decluttered 3D view
  (``generative_studio.rst``, "Director Mode and chat operation"). The chat's
  View menu also docks it left or right, pops it out into a normal OS window and
  returns it to the dock. The chat is its own editor area, never an overlay.
* The conversation menu switches chats and starts a New Chat; each keeps its own
  history, native runtime sessions and draft. Histories and settings live in the
  state folder, outside the .blend file; unsent drafts and attachments are kept
  in memory only.

Transcript
==========

* Assistant replies render their markdown natively (``markdown_blocks`` parses,
  ``chat_replies`` lays out and draws): paragraphs, headings, bullet and
  numbered lists, block quotes, inline code as a mono chip, teal links (opened
  only when http(s)) and fenced code blocks in a dark panel with the language
  and Copy. A reply's layout is cached by its text, width and size, so long
  chats do not re-wrap every frame; a fence still streaming draws as an open
  block without Copy. In Arabic the layout mirrors; code stays left to right.
* ``notice_kinds`` classifies an error row (team sentences by template, a
  Codex error by the prefix ``provider_errors.codex_error_text`` gives it from
  its ``codexErrorInfo``, other provider text by pattern): sign-in expired (the
  team's sign-in or this chat's Connect row), not enough credits, usage limit,
  too many requests, model unavailable, conversation too long, provider busy
  and can't reach the provider each have their own title; "Something went
  wrong" is only for an unknown error. While a Codex turn a throttle or an
  overloaded provider ended waits for its one automatic resend, the live row
  counts down ("Provider busy — retrying in N s") with Stop available; while
  Codex retries a turn itself it says "Provider busy, retrying…"
  (``gpt_launch.rst``, "Failed turns, provider limits and the one resend").
  Display only: the stored row and what the agent sees never change. The
  reconnect row above the composer reserves its height, so a long transcript
  ends above it.
* Error rows are notice cards (``chat_notices``, drawn by the kit's inline
  notice ``ui_kit_status.notice``), never reply text: an amber
  (usage limit) or coral (failure) mark, a plain title, one sentence, the
  existing actions (Switch connection, Connect/Use another subscription, only
  on the latest row) and the provider's own text under a collapsed Details.
  An error that is already one of Angelo's translated sentences is the title;
  a provider's short plain sentence is the card's sentence (no Details), and
  only technical text (exception names, stack lines, JSON, ids) is collapsed.
* Generated media sits in a framed, rounded tile (``studio_chat``).
* The transcript has a thin auto-hiding scrollbar (``chat_scrollbar``): it
  shows while the pointer is over the transcript and briefly after a scroll,
  widens under the pointer, drags proportionally and jumps on a track click.
  Wheel and trackpad panning scroll.
* The canvas lays out in unscaled pixels and draws through one scale factor,
  ``chat_view.ui_scale()`` (the interface scale with the display's pixel
  size): shapes scale with the GPU matrix, text is rasterised at the scaled
  size, scissors and pointer positions are converted at their single call
  sites. Fonts: San Francisco and Menlo where macOS has them, otherwise
  Blender's bundled UI face (Inter) and DejaVu Sans Mono. The accent is the
  brand teal (``ui_tokens``).
* ``test_native_markdown_blocks.py`` pins the parser (including every prefix of
  a streaming reply); ``test_blender_chat_replies.py`` captures a markdown
  reply, a streaming fence, a media tile, every notice kind, Details, a long chat with the
  scrollbar and its drag, Arabic, a narrow chat and a 1.5 interface scale.

Composer
========

* Controls, in order: + (attach a file, or start from a base mesh), approval policy, model and effort (with the Fast
  lightning when the model has a Fast tier), the microphone (only when voice is
  enabled and can work) and Send/Stop. An empty chat with no connection shows
  its connect options instead. Enter sends, Shift+Enter inserts a line, and a
  message is at most 32,000 characters.
* The model chip shows the runtime catalog's name for the model, including a
  full ID the catalog lists under an alias (``model_profiles``); an unknown ID
  is shown as it is. It opens a reasoning card: effort title, model breadcrumb,
  Reset, the Fast lightning and a discrete slider with only the efforts the
  model advertises. The breadcrumb opens Select model: one Default row scoped
  to the chat's connection, then searchable, collapsible provider groups with
  account and billing labels. Escape or a click outside closes a popup without
  typing, sending or scrolling the transcript behind it. The slider animates at
  60 Hz only while the card is open, and not at all with Reduce Motion.
* A model, effort, Fast or connection choice made during a request applies to
  the next message; the running request keeps its captured configuration.
* Writing a message in a chat with no running agent runtime starts one in the
  background (``runtime_prewarm``), so Send does not wait for the launch. Nothing
  is sent before Send; emptying the draft, leaving it unchanged for two minutes
  or writing in another chat closes it again (``performance.rst``, "Runtime
  prewarm").

Models and connections
======================

One chat lists the models of every ready coding connection. Switching keeps the
chat's history, draft, references and snapshots; each connection and model
resumes its own native session and receives only the public turns and results
it missed (``model_routing.rst``, "Per-model native contexts"). Changing
connection turns Fast off.

Connections are managed in ``Preferences > AI Providers`` and from the chat's
connect options (``ai_providers.rst``: sign-in and API-key methods, saved keys,
detected sign-ins, team connections and adapters). No connection is a billing
fallback for another. Generative AI connections never enter the chat's model
list or collaborators; see ``generative_studio.rst`` and
``generation_flows.rst``.

Show and hide
=============

In Manual mode a toggle in the top bar's right-hand part, just left of the
Director / Manual menu, shows or hides the chat; Ctrl+Option+N (Ctrl+Alt+N) does the same
from anywhere in the window, also while typing in the chat (``chat_toggle``).
The toggle is labelled **Chat** (since 1.0.3: 1.0.2's bare icon went unnoticed),
is pressed while the chat is shown, its icon is a speech bubble, filled while
shown and outlined while hidden, and its tooltip says what a click does (*Show
the Angelo chat* / *Hide the Angelo chat*).

A one-time hint says where it is (``chat_hint``, since 1.0.3). It is a kit card
standing right under the toggle, with a small arrow pointing up at it, in
whichever editor lies under it (the chat, the Outliner, the 3D view). The toggle
is the first item of the top bar's right-hand region, which Blender sizes to its
content, at a measured width (``chat_toggle.button_units``), so its rectangle
follows from that region (``chat_toggle.anchor_rect``, recorded at each draw);
the GUI suite checks those pixels show the pressed button. The card is centred on
the button and kept inside its editor; when no editor under the button is wide
enough it falls back to the top right of the largest 3D view, beside the
navigation gizmo, without the arrow. Its text places the button by what is beside
it ("in the top bar, next to Manual", the mode menu's own label), never by a
corner. It is non-modal: it takes only presses on itself (its Hide chat / Show
chat runs after the click, because hiding may close the very area it stands in),
and it waits behind a crash report, What's New and an update notice:

* **Show or hide the chat**, the first time the person is in Manual mode,
  whether they switched to it or Angelo started in it (a 1.0.2 user sees it once
  after updating).
* **The chat is hidden**, the first time the chat disappears in Manual mode by
  any means: the toggle, the shortcut, Blender's Close Area, its pop-out window
  closing. A visible first hint becomes this one. It closes by itself when the
  chat is shown again; a waiting one that no longer applies is withdrawn.

The card draws the top bar's Chat button as it looks and names the shortcut as
it is bound now, read from the user keymap (macOS spells Option and Cmd); a
removed shortcut is not quoted. Its primary button does what the toggle would
(*Hide chat* / *Show chat*); *Got it* and the close button dismiss it. A
dismissed hint is remembered per user in ``chat-hints.json`` in the state
folder, and dismissing *The chat is hidden* covers both. A hint never seen
(Angelo quit, its 3D view went away) shows again. Director never shows one, and
switching to Director closes a visible one. Nothing shows in a background or
automation run unless ``ANGELO_ENABLE_CHAT_HINTS=1`` (its own GUI suite).

* Hide closes the chat's area with Blender's own Close Area, so its neighbours
  take the room, after keeping the draft; a popped-out chat's window closes the
  same way. The conversation never changes.
* Show docks the chat on its last side (``ui.request_chat_dock``) with that
  draft. Window > Angelo Chat still opens it too. When the outer column on that
  side is wide but the 3D view has no room left (Blender's own Close Area on the
  chat can hand its room to the Properties/Outliner column), the chat takes its
  width from that column instead of failing (``layout.plan_dock``). A Show that
  still can't dock says why on a corner card (*The chat couldn't open*), since
  the hidden chat can't show its own error.
* An Angelo corner card (the update notice, this hint) never makes docking or a
  mode change wait (``input_policy.layout_blocking``); a modal card, a real tool
  and Blender's window screenshot still do. Before 1.0.3 the hint's own Show
  chat, and Show or Director beside an update notice, refused or waited.
* Director always shows the chat beside the 3D view: the toggle is not drawn
  there and the shortcut says to switch to Manual mode.

Ctrl+Option+N is free in every non-modal keymap of Blender's keymaps, like
Director's Ctrl+Option+M; both are defined once in ``shortcuts.py`` and live in
the add-on keyconfig (``test_native_shortcuts``, see the Director shortcut in
:doc:`generative_studio`).
``test_native_chat_toggle`` and ``test_blender_chat_toggle`` cover the toggle,
``test_native_chat_hint`` and ``test_blender_chat_hint`` the label and the hint.

When the chat can't start
=========================

A chat that looks ready never has a dead click. Each reason it can't start yet
is shown where the person is looking, as the kit's inline notice with the one
action that fixes it (``chat_start_states``):

* **No connection**: the connect options replace the composer (above).
* **Connected, no model.** A chat is never bound behind the person's back: a
  connection added elsewhere (AI Providers, a confirmed suggestion) binds no
  chat, and an unbound chat adopts the store's active connection of its mode
  when it sends or opens the model list (``ai_providers.rst``). Once bound, its models
  load and the catalog's default shows; a connection offering exactly one model
  has that one chosen. With models of its own and none flagged default, the
  connection's default runs, so the model chip says *Default*, as the model
  list's checked Default row does. When the chat has no models of its own, the
  empty chat's welcome shows a card between its subtitle and the example
  prompts: *Loading your models…* (no action; it ends by itself), *Couldn't
  load your models* (Retry, with the provider's text under Details), *No models
  available* (Retry), or, when other ready connections offer models, *Choose a
  model to start* (Choose a model, which opens the model list). The model chip and the
  card share one guarded path (``chat_welcome.open_models``): a connection that
  can't be prepared becomes the chat's notice row instead of an ignored click.
* **Another tool holds the window's input.** The chat leaves its events to any
  running modal operator (a transform, a render, an add-on's modal tool) so it
  never takes a key or click that tool expects. Angelo's own passive overlays
  are not such tools: the welcome screen, notice cards and the AI Providers page
  take only the events over themselves (``input_policy.PASSIVE_OVERLAYS``), so
  the chat beside them stays usable. Layout changes and Blender's own window
  screenshot (whose pixels would hold the overlay) still wait for them
  (``interaction_active``); the agent's offscreen captures, renders, sculpt and
  program jobs do not (``tool_running``). Keys typed with the
  pointer over an open overlay are the overlay's. Every Angelo modal operator is
  classified as a canvas input, a passive overlay or a holding tool (an approval
  waiting for Return or Esc); a test fails for an unclassified one. A click on the chat while a real tool
  holds the input shows *The chat is waiting for another tool*, naming it and
  saying how to end it (Esc, or turning off an add-on that keeps running). It has no button, since the chat's clicks are what
  the tool holds, and goes with the chat's first event after the tool ends. The
  chat's input handler starts even beside a running tool, passing its events
  on, so this holds for a tool that was running first.
* An open picker that fails to draw is closed, so it can never take clicks
  while showing nothing. An event that raises in the chat's input handler is
  logged and passed on rather than ending the handler, and a handler that ended
  anyway is started again (``input_policy.handler_running``).
* The setup card's settings import waits for a running turn, since the chat
  now works beside the card.

Before 1.0.2 the chat counted the welcome screen and notice cards as a tool in
progress: on a first launch, with the welcome or a What's New card still open
over the 3D view, the drawn chat ignored every click and key until a restart
(which does not reopen them), and a chat connected outside it read "Choose
model". ``test_native_chat_start_states`` covers every state without Blender;
``test_blender_chat_start_states`` proves them in the running app on a real
first launch, with screenshots.

Fast
====

Fast is the provider's paid priority tier, separate from effort, and its
controls appear only for a model whose catalog advertises one. Enabling it asks
for confirmation bound to the current chat, connection and model; effort and
Reset never enable it. Each Codex turn requests the default or the selected
tier explicitly, so Fast is never sticky. Claude Fast uses Claude Code's
``fastMode`` on a supported Opus model and draws paid usage credits, not a
subscription's included usage. Fast turns off when the selected model has no
Fast tier (a catalog change also posts a chat notice) or the connection changes.

Approvals
=========

* **Auto bounded edits** ("Approve for me") is the default for a new chat and
  for a saved chat with no choice (``chat_settings.DEFAULT_APPROVAL``): it
  approves validated typed edits, programs, recipes, preview Accept/Discard,
  native sculpt passes and flow edits, and nothing else. **Ask for approval**
  ("Ask before changes") reviews each scene mutation. **Read only** rejects
  every mutation, including Python, and cancels queued changes. A chat keeps
  the mode saved with it; a new chat never inherits another chat's choice.
  Only a missing mode takes the default: any other value outside the three
  (a typo, a mode from a later build) reads as Ask, and ``angelo.set_approval``
  refuses it. External CLI conversations are created in Ask
  (``external_cli.rst``).
* **One-time move to Auto.** A chat saved before Auto became the default holds
  Ask only because that was the old default: loading it moves it to Auto once
  and saves ``approval_version: 2`` with it (``migrate_saved_approval``; a later
  version also counts as moved), so an Ask chosen afterwards survives reloads.
  Read only and External CLI conversations are never changed. A build older
  than the marker drops it when it saves the chat, which would make a
  deliberate Ask look like the old default again; every 1.0 build writes the
  marker.
* ``RequireAskForEdits`` on, as a managed or organization policy
  (``enterprise_policy.rst``), makes every chat Ask: Auto reads as Ask
  everywhere (``clean_options``), and choosing it says "Turned off by your
  organization." instead. The chat keeps the person's own mode
  (``stored_options``; the policy is applied when the mode is read, never
  saved), so it is back in that mode when the policy lifts. ``AllowAutoApproval``
  off is separate: edits stay in Auto, and no script runs without its own
  review.
* Python needs exact-code review unless the person turns on **Trust scripts for
  this chat** (``agent_execution.rst``, "Trusted scripts for an open chat") or,
  in Auto, **Always allow scripts** ("Always allow scripts" there). The script
  approval card in an Auto chat offers it beside Approve and Decline, with a
  note that scripts have full access to files, the network and other apps on
  this computer and that Preferences can turn it off; Preferences > AI
  Providers and the add-on preferences show the toggle (or the organization's
  lock). With it on, the composer's label in an Auto chat reads "Approve for me
  · scripts allowed" and the approval popover's footer "Scripts run without
  asking in Auto." Only the person changes it (``agent_execution.rst``, "Always
  allow scripts"), and a script that refers to preferences or to Angelo's own
  add-on always asks. ``angelo_execute_python`` is not a sandbox: approved code
  has Blender's file, network and process privileges, and Stop cannot
  interrupt a running script.
  File writes and deletions need individual approval in every mode. Applying a
  scene change requires Blender's Global Undo. Tool calls dispatch to a bounded
  main-thread queue; ``agent_execution.rst`` specifies the tools.
* Approval cards describe the change in plain language, naming objects rather
  than handles, with the exact code or parameters under Show code or Show
  details. The card's single Approve button never looks disabled; for an
  unreviewed script it reads **Review & approve**, and its first click only
  reveals the code.
* The decision cards (approval, native preview, work on a copy) are drawn by
  ``chat_card_draw`` in one anatomy: a status chip (amber "Needs your
  approval" with an amber rail; teal "Preview · not applied" or "Working on a
  copy"; the kit's status chips, plus a paid chip on approvals that spend API
  credits), the action as the title, plain rows, technical detail in a framed
  block, and one action row at the card's bottom (teal primary, quiet
  secondary, a text toggle). The hits keep their actions, so approval and
  preview semantics are unchanged. A program's steps are grouped by
  consecutive kind and counted ("2–33. Material principled · “Red”, “Blue”
  and 30 more", ``approval_steps``). Details stay the complete exact review;
  in a right-to-left language they draw with the shaping font, because the
  mono font has no Arabic forms. A
  preview's summary is counted change chips and plain sentences ("1 object
  added", "All 5 checks passed"); when its record is gone, Accept and Discard
  are disabled with the reason on hover. The job card shows a teal progress
  bar and "Step 2 of 12", and its height is measured so Stop never covers
  text; the changes pill measures its counts. The cards lay out in the
  canvas's unscaled pixels and draw under its one scale factor
  (``chat_view.ui_scale``); a program's review rows are prepared for that
  same scaled width (``approval_review.prepare_canvas_reviews``), or the card
  would never appear at a UI scale other than 1. Details keys known to
  ``detail_keys`` ("Kind", "Target", "Material identity") show in the
  interface language; values, names and numbers stay exactly as submitted.
* A Generation Flow's run approval (``flow_chat.approval_layout``) uses the
  same language: amber rail, "Needs your approval" and "Uses API credits"
  chips, "Run Generation Flow “Hero”" as the title, a teal Run button,
  Decline and Details.
* Accept on a native preview opens **Accept this preview?** as a centred
  Angelo card over the 3D view (``review_card`` on ``card_host``), not a stock
  dialog: what the preview does in plain words (``review_text``: Added,
  Changed and Removed objects by name with their kind, "Mesh · 8 vertices",
  "Objects in the scene: 3 → 4", every check with Passed or Failed, and a
  caveat only when changes are not listed or not read back), the exact
  submitted settings one click away in a mono box, the "I reviewed this exact
  result" checkbox, Cancel and Accept. The words are display only: the
  operator's review lines, the review fingerprint and what the agent receives
  are unchanged. ``ANGELO_OT_accept_native_preview``
  still prepares the review in ``invoke``, waits (modal) for the card's
  decision, and runs its unchanged ``execute``: Accept without the box ticked
  is refused, Return accepts and Escape cancels, as the dialog's keys did.

Transcript activity
===================

Two or more consecutive steps of a turn fold into one work group
(``chat_rows.group_blocks``): "Working on the scene · 6 steps" with a spinner
while it runs (its newest step stays visible under it), a summary such as
"Made 2 changes · Checked 3 things" when done; a click opens the steps. Each
step row has an icon for its kind of work, a plain-language title and the
kit's status chip (Running, Done, Failed, Needs you, Preview, Reverted). The
titles, kinds and chip states come from ``activity_text``: an unknown tool or
error code falls back to a generic phrase, never its name, and the Claude
runtime's plan call (``StructuredOutput``) reads "Planning the changes".
Host-verified results, script and plan failures and restore outcomes draw as
the kit's inline notice: a friendly headline (an error code or exception class
becomes a sentence) and one suggestion, with the stored text, ids and
exceptions under a collapsed Details; a plan that simply completed is one
muted line. All of it is display only: stored rows and everything the model
reads are unchanged.

Tool rows expand to show the tool, its arguments or code, the actual result and
errors (a plan call shows its operations, never its reply). Thinking rows show only reasoning text the runtime supplies; while the
model reasons, the live row adds the latest title of that summary ("Thinking ·
Planning the stone footbridge", ``plan_progress.summary_title``), so a typed
plan, which shows no reply until it is written, says what it is working on
during the reasoning before it. The title is the runtime's own public summary, drawn as data and never
requested for display; when it arrives within the reasoning is the runtime's
choice and has not been measured. While a round
writes its plan the live row reads "Preparing the next steps" with the plan's
steps under it as they are written, muted, each with a "Planned" chip
("Deck", "Post ×4", "Rail ×2", "Grey Stone"); they are drawn only and leave
with the plan, and a step becomes a real row only when it runs
(``agent_execution.rst``, "How long a turn may run"). The changes pill and
Scene Changes popup show native read-backs (names, transforms, geometry,
materials and modifiers) for at most 1,000 objects and 1,000,000 vertices,
reporting truncation. They are not a complete RNA audit or a live diff after
manual edits or Undo, and external effects are not tracked.

While Claude Code streams tool input, a Claude plan's reply is drawn in its
chat row as the model writes it, and kept only when the plan arrives (a plan
that never ran leaves nothing), and a script being written is named in the
live row by its own description (``agent_execution.rst``, "How long a turn
may run").

Waiting for the first step: carved from marble
==============================================

Before a turn runs its first step, the model often plans for 20-60 s. The chat
shows that wait as the Angelo raven carved out of a block of marble, drawn by
the chat's own GPU canvas (``marble_mark``, ``marble_carving``, ``marble_wait``,
``marble_draw``, ``marble_host``):

* **Two sizes.** A hero mark (112 px at a UI scale of 1) in its own block under
  the turn's request, and a 22 px raven on the live row where its spinner was.
  The hero shrinks to 56 px once the first step runs and leaves with the turn; a
  voice turn, and the first frame of a turn before the timer has seen it, keep
  the spinner.
* **Carving.** The block is a jittered grid of triangular chips (8 x 8 cells on
  the hero, 4 x 4 on the icon), pale stone with a darker edge per chip and a
  few grey veins on the hero. Chips fall outside-in on a steady schedule that
  approaches 72% of the block as ``1 - exp(-t / 22 s)``, so they slow as the
  block thins and a long wait never runs out of stone.
* **Real events only.** A chisel tap (a burst of 5% of the chips, a little dust
  and a small shake, never past 80%, at most one per 0.5 s) marks the
  progress the chat already has: the plan call starting ("Preparing the next
  steps"), more provisional plan rows (``plan_steps``), and a new line of the
  runtime's thinking summary in the live row (``plan_progress``). The reveal is
  the turn's first step: the first tool row after its request, or the plan
  call's row once the runtime accepted it (a refused plan is no step). The rest
  of the stone falls, the raven looks up and blinks, and from then on the live
  row's raven is cocked 7° and blinks every 3.8 s while steps run. Nothing is
  timed to look like progress.
* **Thinking summaries.** A GPT summary names the live row by its title
  (``summary_title``). A Claude thinking block without titles names it by its
  latest finished sentence (``summary_line``, at most 64 characters, cut at a
  space): the hook is in place for runtimes that stream summary text, and
  inert while Claude Code omits it, as it does for Opus 5.5. Either way the
  line is the runtime's own text, drawn as data, and a new line is a tap.
* **Reduce Motion.** Blender's Reduce Motion preference, or the system's
  (macOS Reduce motion, Windows "Show animations" off, ``motion_preference``),
  asked once per turn: chips fade where they stand (no flight, dust or shake),
  the raven does not blink or tilt in motion, and the hero's size changes in
  one step.
* **Cost.** ``marble_host``'s timer exists only while the drawn chat runs a
  turn (started by the chat's existing 0.1 s poll) and asks for a frame only
  while something moves, at most 24 a second. A frame does not lay the chat out
  again: while a wait runs the chat's full draw is kept as a texture
  (``marble_frames``), and a frame the timer asked for draws that texture and
  the two marks over it. Frames are paced to at most 2% of the main thread by
  their measured cost, and the animation pauses while the person orbits,
  scrolls a view, drags or runs any other modal tool, during playback, when the
  window is out of focus and when no mark is in view. It reads the controller
  and never writes it: the provider threads, the event pump and native dispatch
  are untouched. The measured cost and the ratchet operations are in
  ``performance.rst`` ("Chat waiting animation").

``test_native_marble_waiting`` covers the geometry, schedule, events, reveal and
frame scheduling without Blender; ``test_blender_chat_waiting`` runs a real turn
through the stand-in Claude CLI and checks state, frames and pixels at both
sizes, before and after the plan arrives, and with Reduce Motion.

Attachments and drops
=====================

Attach PNG, JPEG or WebP images; UTF-8 text and code (``.txt``, ``.md``,
``.rst``, ``.py``, ``.json``, ``.csv``, ``.yaml``, ``.obj``, ``.mtl``, ``.svg``,
``.gltf``); PDF; or ``.blend``, ``.glb``, ``.fbx`` and ``.stl`` assets: up to
four files per message, images up to 5 MiB and other files up to 20 MiB. Each
is copied privately into the state folder, never into the project. Images are
sent as image inputs. Text is untrusted context: 12,000 characters go with the
message and ``angelo_read_reference`` reads further excerpts of up to 32,000.
PDF text comes from an installed ``pdftotext`` (first 64 pages, at most 30 s,
no OCR), never on Blender's main thread: the provider worker extracts the PDFs
of the last 80 messages before a turn connects, and ``angelo_read_reference``
(which runs on the main thread) answers ``"ready": false`` with a note to ask
again while a background thread extracts one it has not seen. Each attached
file is extracted once (``ReferenceStore.content``, keyed by the snapshot's
name, size and modification time): a failure or time-out is kept for the
session rather than retried every turn; attaching the file again retries it,
and a missing ``pdftotext`` is never kept. 3D assets are references only, never
imported or executed automatically; importing one takes a Python script under
the approval rules.

Files dropped on the chat attach through Blender's native multi-file
``FileHandler``; a dropped .blend is attached, not opened. A busy turn rejects
attachments without side effects, and typing or pasting a path never attaches.

Start from a base mesh
======================

The composer's **+** opens Angelo's menu (``chat_add_menu``, popover kind ``"add"``): **Attach** (the file
browser, as before) and **Start from a base mesh**, one row for each of the 21 bundled Human Base Meshes
(``human_base_meshes``; heads first, each with its height and vertex count). With a mesh selected, an opt-in
toggle, "Match the size of <name>", fits the pick to that object's centre and height; otherwise it stands at
the 3D cursor at real size. The menu scrolls, takes the arrow keys and Return, and closes on Escape or a click
outside. A pick becomes a teal chip beside the attachments, and its **×** removes it.

At Send, before the observation is built, ``chosen_tools_host`` places the pick with ``asset_start.place``. That
is the operation a model's typed create will share: it appends every object the asset holds, removes the file's
collections, tags each object ``angelo_base_mesh`` and leaves the main object selected and active, all in one
Undo step ("Start from a base mesh"). One chat row says what was added and how long it took. The model learns of
it from the scene facts it already receives (the new objects in the observation); nothing in its instructions,
schemas, budgets or observation fields changes. A pick that fails removes what it
added and says why, and the message still goes. The step runs before the request checkpoint, so restoring
that checkpoint keeps the mesh; its own Undo step removes it.

Tests: ``test_native_chosen_tools`` (rows, choices, layout, keys) and ``test_blender_chosen_tools`` (GUI: the
menu as drawn, scrolling, the chip, the fit, the Send step, every base mesh timed, the staged-context path, a
failing pick).

Collaborators
=============

The lead model can call ``angelo_delegate`` with a ready coding connection, an
advertised model and a focused question. The consultation runs off Blender's UI
thread and its reply and tool activity appear as rows in the same chat. Its
approval card names the account whose quota or API billing it uses; under
standing consent ("Run fast helper models without asking", on by default) a
fast-tier model on the chat's own connection runs without the click, still
shown and recorded (``model_routing.rst``, "Fast helpers").

A collaborator is read-only: it can inspect the scene, attached references and
scoped files and use its runtime's web tools, but cannot mutate, run Python,
write files or delegate again. Limits: two consultations per request, one
level, 120 seconds each, Fast off. Stop, a document change or an invalidated
connection cancels it, and late results change nothing. Managed policy
``AllowCollaborators`` can turn delegation off (``enterprise_policy.rst``).

Web and file tools
==================

Codex connections use Codex's live web search where the provider supports it;
otherwise it is disabled and the model is told so. Claude Code runs with only
its WebSearch and WebFetch built-ins, an empty MCP configuration and no setting
sources, on every Claude connection: subscription, API key and team launch
alike (``providers.claude_session``). None uses Claude Code's ``--bare`` mode,
whose built-in tools are only the shell and file tools (``--tools`` filters
within them), so it would leave no web tool. With no setting sources, no
``CLAUDE.md``, ``CLAUDE.local.md``, ``AGENTS.md``, rules, subagents, hooks or
project settings reach the model, whether from the session's working folder
(its private workspace in the state folder), that folder's parents or the
connection's profile. Unbilled captures of the API-key and team launches with
all of these planted showed none of them; a control with setting sources on
showed them. Claude Code always applies the computer's managed (policy)
settings, which an administrator installs, including their hooks and ``env``
block, as in every Claude Code session. An inherited ``CLAUDE_CODE_SIMPLE``,
which turns bare mode on, is dropped (``runtime_environment.TOOL_POOL``).
A Claude session launched without
WebSearch is told that native web search is unavailable, as on Codex. WebFetch
refuses ``localhost``, ``.local`` hosts, private IP addresses and URLs with
credentials. Results and file contents are untrusted data.

On an API-key or team connection, by Claude Code 2.1.293's code (unbilled
captures show the tools; no search or fetch has run live on these routes yet):
WebSearch is Anthropic's server-side web search tool, called through the
connection's endpoint (for a team, its gateway) and billed per search to that
key's organization, whose Console must allow web search. WebFetch first checks
the domain with an unauthenticated call to ``api.anthropic.com`` that does not
go through ``ANTHROPIC_BASE_URL``, then summarizes the page with a model call
through the connection's endpoint. A network that reaches only the gateway
therefore fails WebFetch's check. Claude Code's non-essential traffic also goes
to ``api.anthropic.com`` directly. In captures through a refusing proxy, each
launch opened four such connections (two with ``--bare``), and none with
``CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1``. Angelo does not set that
variable on any route.

The file tools list a directory (200 entries), read UTF-8 excerpts (131,072
bytes per call), write UTF-8 text (131,072 characters) and delete single files,
on files up to 20 MiB. Their root is the saved project's folder, or the chat's
private workspace in the state folder for an unsaved project. Absolute or
``..`` paths, hidden names, links and junctions, credential and repository
folders (``.git``, ``.ssh``, ``.aws``, ``.codex``, ``.claude``, ``.env*``) and
Angelo's state and account folders are rejected. Writes and deletions are
journaled in the request snapshot; Blender Undo does not revert them.

Request snapshots
=================

By default the main thread saves a private copy of the whole document before
each request; if capture fails, the request does not start. Each user message
offers Restore snapshot, which needs active requests stopped, takes a safety
snapshot and opens a recovery copy with embedded scripts disabled. The original
project is not overwritten (use Save As, or Undo restore to go back); chat
history survives, and journaled file-tool and Studio-export changes since that
point are reverted. Arbitrary approved Python, manual exports and network
effects are not recoverable. Policies, deduplicated storage and retention
(2 GiB by default) are in ``checkpoint_policy.rst``.

Voice
=====

Voice is off by default. The microphone appears only with **Experimental
voice** on in the add-on preferences, on macOS, with a personal Codex
connection (not Claude or a team connection) and installed ``ffmpeg`` and
``ffplay``. It uses Codex app-server's realtime methods with no API fallback,
streams mono 24 kHz PCM, plays the provider's audio and saves no audio. A
confirmation explains streaming and possible provider charges first. There is
no echo cancellation, so use headphones. Managed policy ``AllowVoice`` can turn
voice off. Voice has not been tested with real microphone audio.

Validation
==========

Dependency-free host tests (the full host suite is in ``CONTRIBUTING.md``)::

   python3 -m unittest discover -s tests/python/angelo_agent -p 'test_native_chat_*.py'
   python3 -m unittest discover -s tests/python/angelo_agent -p 'test_native_routing.py'

Native suites against the compiled app, each in fresh isolated state::

   python3 tests/python/angelo_agent/run_native_suites.py \
     --binary "$PWD/../build-angelo-native/bin/Angelo.app/Contents/MacOS/Angelo" \
     --gui --include 'test_blender_chat_*' --include 'test_blender_checkpoints'

These need no login, inference, Fast request or microphone. The LIVE suites
``test_blender_shared_sessions`` and ``test_blender_cross_provider_live``
exercise real Codex and Claude handoff and read-only delegation; they bill the
person's own subscriptions and run only with ``--allow-live``, an exact
``--include`` and their opt-in variables. The standalone GUI fixture
``gui_smoke.py`` makes ordinary-tier subscription turns (a scripted sphere, a
web search, one file write) with ``ANGELO_QA_LIVE=1``, approving only that
exact script and file write::

   ANGELO_QA_DIR=/tmp/angelo-qa ANGELO_STATE_DIR=/tmp/angelo-qa/state \
     ../build-angelo-native/bin/Angelo.app/Contents/MacOS/Angelo \
     --factory-startup --no-window-focus --python tests/python/angelo_agent/gui_smoke.py
