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
  only when http(s)), fenced code blocks in a dark panel with the language
  and Copy, and tables. A reply's layout is cached by its text, width and size, so long
  chats do not re-wrap every frame; a fence still streaming draws as an open
  block without Copy. In Arabic the layout mirrors; code stays left to right.
* Tables follow GitHub's rules (``markdown_blocks``): a header row, then a
  delimiter row of ``---`` cells as many as the header's (``:---``, ``---:``
  and ``:---:`` align a column); outer pipes are optional, ``\|`` is a literal
  pipe, a short body row is padded and extra cells are dropped, and the table
  ends at a blank line or the first line without a pipe. A line with pipes and
  no delimiter row under it stays a paragraph; a delimiter row still streaming
  (the reply's unfinished last line) already draws the header. ``chat_tables``
  lays the table out: a grid with the header on a light fill, each column as
  wide as its widest cell; when that is wider than the chat, columns narrower
  than their fair share keep their width and the widest share the rest, wrapping
  their text between words. A column is never narrower than its longest word
  (a token wider than 180 px, such as a URL, may break). When the columns can't
  all hold their longest words, or the chat has less than 56 px of text per
  column, the table stacks instead, one record per row with each cell as
  "Header: value", so nothing is clipped and no word is cut in two. Inline code, links and emphasis work inside cells; a cell with
  right-to-left text is drawn whole, and Arabic mirrors the grid. The reply's
  Copy copies its markdown source, tables included (``markdown_blocks.plain_text``
  gives the cells tab-separated).
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
  Wheel and trackpad panning scroll, in the system's scroll direction (macOS
  natural scrolling, the Windows touchpad setting, libinput's natural
  scrolling on Linux): every Angelo-drawn scrolling surface (the transcript,
  the chat's pickers, card scroll boxes, the Media Studio editor's columns and
  lists) asks ``scroll_input.view_up``, which moves the view as Blender's own
  ``view2d`` does for the same event, by the same distance (on macOS one
  pixel per pixel). No surface reads a pointer delta itself
  (``test_native_scroll_and_caret.py``).
* The canvas lays out in unscaled pixels and draws through one scale factor,
  ``chat_view.ui_scale()`` (the interface scale with the display's pixel
  size): shapes scale with the GPU matrix, text is rasterised at the scaled
  size, scissors and pointer positions are converted at their single call
  sites. Text is measured as it is drawn, through ``chat_view.measure`` (or
  ``ui_kit.measure`` for a kit view): glyph advances are whole pixels at the
  rasterised size, so a width measured at the unscaled size drifts from the
  drawn glyphs by up to half a pixel per character. Wrapping, centring, the
  composer's caret, selection and click-to-caret all measure there. Fonts: San Francisco and Menlo where macOS has them, otherwise
  Blender's bundled UI face (Inter) and DejaVu Sans Mono. The accent is the
  brand teal (``ui_tokens``).
* ``test_native_markdown_blocks.py`` pins the parser (including every prefix of
  a streaming reply and of a streaming table); ``test_native_chat_tables.py``
  lays tables of several shapes and scripts out at chat widths from 120 to
  1200 px with the real wrapping, and checks that every cell's text is drawn
  inside its column, no ordinary word is cut in two, and nothing is lost when
  the table stacks;
  ``test_native_activity_text.py`` (``TenseTests``) checks every titled tool in
  every state on both title paths (past when done, progressive when running,
  neither otherwise); ``test_native_chat_start_states.py``
  (``NewChatModelTests``) runs ``ui.create_session`` over a real Controller; ``test_blender_chat_replies.py`` captures a markdown
  reply, a streaming fence, a media tile, every notice kind, Details, a long chat with the
  scrollbar and its drag, Arabic, a narrow chat and a 1.5 interface scale.

Composer
========

* Controls, in order: + (Angelo's navigation: attach a file, Media Studio, Generation Flows, a base mesh, and
  every registered feature; see "The + menu"), approval policy, model and effort (with the Fast
  lightning when the model has a Fast tier), the microphone (only when voice is
  enabled and can work) and Send/Stop. An empty chat with no connection shows
  its connect options instead. Enter sends, Shift+Enter inserts a line, and a
  message is at most 32,000 characters.
* Keyboard focus: the composer takes the keyboard when it appears and on a click in it, and keeps it while the
  pointer drifts over another editor, so a long message loses no keystroke (view-navigation keys still follow the
  pointer, ``input_policy.POINTER_KEYS``). Escape ends editing as in a Blender text field, but keeps the draft and
  clears the selection; after it every key goes to the editor under the pointer (Z over the 3D view opens the
  shading pie) until a click in the composer. A click outside the composer, orbiting, scrolling or a context menu
  elsewhere also ends editing. With a toolbar control focused by Tab, Escape first returns to the field; with a
  picker open, Escape closes the picker (or clears its search first). Keys pressed with the pointer over the chat
  go to the chat, as to any editor under the pointer. Media Studio's fields and the kit's fields
  (``ui_kit_field``) end editing on Escape the same way; ``test_native_field_escape`` drives each one.
* In a narrow chat the toolbar gives its room by priority (``chat_composer.footer_fit``): the model chip names
  the model whole (its effort goes first), the approval policy keeps its label only where it fits beside the
  chip and is otherwise its icon alone; only a chat too narrow for the icon and the chip elides the model. Each
  control's hit, and so its hover and focus outline, is what it draws (the approval control is its icon and
  label). Keyboard focus hides the hover of a pointer that has not moved since, so one indicator shows at a
  time, and a tooltip for a composer control opens above the composer, never over its text
  (``canvas_controls.tip_rect``). Every picker opened from the toolbar opens above the composer's top, as the
  + menu does (``chat_popovers.popup_layout``'s floor). ``test_blender_canvas_states`` and
  ``test_blender_chat_start_states`` read these from what is drawn.
* A chat with nothing about its document (no message besides host notices, turn, tool call, native preview,
  native thread or remembered brief or result) follows a newly opened document quietly; a chat with any of
  them says "Document changed. Start New Chat before issuing scene commands." and keeps its turns bound to
  their document (``Controller.set_document``; ``test_blender_fresh_chat_document`` opens a file after startup).
* Opening the *same document* again keeps the chat (``document_identity``). A document's identity is a random
  id saved in the file (the ``_angelo_document_id`` custom property of every local scene, written at each Save,
  never at a home-file save or a request snapshot, never by opening a file) and the path the document was read from
  or last saved to (``bpy.data.filepath``, after a Restore too: ``checkpoint_policy.rst``, "The document after a
  Restore"). A Save As moves it; a Save Copy, Save Startup File and a save into Angelo's state folder never do (Auto
  Save and quit.blend run no save handler). The same document is the same id,
  at the same path as written, in the same file, read only from the open document's own file at its load and save
  (``document_identity.file_identity``): its file number and creation time on macOS, where the device number
  changes when an external drive or disk image is mounted again; the volume serial and file index on Windows; the
  device and inode on Linux. A retargeted symlink, a same-named volume, a reused drive letter or a copy made by
  ``cp -p``, ``ditto``, ``rsync -t`` or Finder is another file. A known limit, by construction: a byte-identical
  block-level clone of the volume (``dd``, ``asr``, a duplicated disk image, a cloned SD card) mounted at the same
  path holds the same file number and creation time, and so does a copy on FAT whose creation time was set to the
  original's second on purpose; no metadata tells them apart, so such a clone reads as the chat's own file. Revert, Open Recent of the chat's file,
  "Continue where you left off" (``splash_setup.open_start`` opens the file the same way), and Recover Last Session
  or Recover Auto Save (they read the original path back) therefore rebind the chat to its own scene (by the name the
  file holds, else the file's only scene, else its scene reads as missing): what the earlier load staged or queued
  is revoked, a running turn stops, its memory is stale (``restart``) so the agent observes the scene again, and a
  lead's worker batch ends and its workers stop as for another file (``worker_ending.reloaded``; Restore and Undo
  restore end them too: nothing built before the content changed merges). Another file is another document and
  invalidates the chat as before: a file with another id or none (saved before Angelo kept one, or by another
  program after a conflicting append), the same id at another path (a Finder copy, the file a Save As left
  behind, the original moved or renamed) and every unsaved file (File > New, whatever its home file
  carries). When the chat's own path comes back with the same id in a file that cannot confirm it (a file system
  that reports no file number, or a file rewritten by another tool such as a checkout or a sync), the chat is not
  told it changed: it waits for its file and asks "{file} changed outside Angelo since this chat last worked on
  it. Continue this chat with it?" with Continue. Whatever the outcome (rebound, asked, or changed), a successful
  load ends a lead's worker batch and stops its workers once, before the chat's fate is decided, so a Continue never
  resumes anything built before the load; a failed load ends nothing. When that stop itself fails, the chat says so,
  and no merge of a part from a worker left running is admitted (Auto included: ``worker_merge`` refuses it) until
  that worker is stopped or discarded. A load that fails
  read nothing: its chats, on a saved or an unsaved document, stay on their scene with
  nothing else changed (a running turn it stopped at ``load_pre`` stays stopped). When a lead's workers cannot all
  be stopped, its chat says so. One chat's failure never stops another's
  binding or the rest of the load; a save that fails while binding leaves the chat bound, its state in memory, and
  the chat says once "Angelo couldn't save this chat…". The decision waits for the load's end only in a process that
  runs it (``ui.document_load_post`` and the failed-load handler registered: ``document_identity._completes``); one
  that registers ``ui.document_load_pre`` alone (``headless_main._register``) tells every chat at ``load_pre``, as
  before, so no chat is left undecided. ``test_native_document_identity`` and
  ``test_native_document_identity_breakit`` cover each.
* Each chat saves the file it works on with its conversation (``document_file``: the id, the path and its scene's
  name as the file holds it; host-only, never sent to a model). After a relaunch, a chat that holds work about a
  file other than the open one waits for it instead of binding to the startup document: its composer says "This
  chat works on {file}. Open that file to continue, or start a new chat." (or, for a file never saved, that it
  can't be opened again), its requests, voice and Restore are refused, and opening that file binds it as a reopen
  does. When the open file carries its file's id but is another file, the chat asks "This chat worked on {old}.
  Continue it with {new}?" with Continue (``document_offer``); only that click moves it to the open file. The two
  files are named so they can be told apart (their names, else as many trailing folders as it takes, cut from the
  left to fit), and a card is matched to the offer by both full paths, never by the names shown. The offer, its
  refusals and a withdrawn offer's row are the person's UI only, never a model's context (``routing.handoff``,
  connected apps' task progress); after Continue the person reads "This chat now works on {file}." and the model
  reads one fact (``ui_text.model_notice``): that the person continued the chat with that file, the same document
  moved or changed outside Angelo, and that it should read the scene again before relying on earlier results. Continue
  shows on every card naming the open file while the offer stands (never a stale one: a Save As or another load
  withdraws it, and a click on a withdrawn one says so), a request the chat refuses asks the same question, and
  each load of that file asks again. Another file leaves it waiting.
  An app's MCP conversation restored when the app reconnects follows the same rule. A chat saved before this record
  existed binds as before. A background Angelo (a worker, a headless host) keeps 1.0.6's rule: every load is
  another document.
* With an empty draft the composer shows a hint: "Do anything", an external client's limits, or, while the chat's
  scene is not shown, which scene it works on and how back (``document_scene.AWAY``, ``GONE``). The hint is wrapped
  as the chat wraps a message, in reading order (an Arabic hint's first row reads first; the draft alone wraps by
  source offsets), and the composer grows for it up to the draft's eight rows (``chat_composer.hint_rows``), so no
  hint is cut mid-sentence in any language; a chat too narrow for it shows the whole sentences that fit, then an
  ellipsis (a sentence ends where its script ends it: Chinese and Japanese full-width ends need no space,
  ``chat_composer.sentence_ends``), else as much as fits with the ellipsis measured on its row.
  ``test_blender_session_rail`` draws the away hint and the widest hint of every shipped catalogue at 1x
  and 2x and judges each against the width it was drawn at (``composer_hint_qa.as_the_chat_allows``): whole when
  the whole hint fits the eight rows there, else the sentences that fit, else every row. A chat's width in layout
  units is its pixels over the interface scale times the display's pixel size, so the same 2x window is half as wide
  on a 1x Windows display as on a Retina Mac; ``test_native_composer_hint_fit`` covers both across displays.
* The empty chat's welcome (heading, connect options or examples, links) is one column, at most 420 units wide,
  centred in the chat with at least a 24-unit margin (``chat_welcome.draw_empty``).
* The caret is a character index into the draft, as
  ``ConsoleLine.current_character`` reads and writes it (only the
  ``console.history_append`` operator takes a UTF-8 byte offset:
  ``drafts.console_byte_offset``). ``chat_composer.column_x`` places a column
  where its glyphs are drawn and ``column_at`` is its inverse, so the drawn
  caret, the selection, a click and Up/Down (which keep the caret's x) agree
  with the text. ``test_blender_chat_scroll_caret.py`` checks both in the
  running app against Blender's own Outliner and the app's own glyph rendering.
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
fallback for another. Media generation connections never enter the chat's model
list or collaborators; see ``generative_studio.rst`` and
``generation_flows.rst``.

Show and hide
=============

In both modes a toggle in the top bar's right-hand part, just left of the
Director / Manual menu, shows or hides the chat; Ctrl+Option+N (Ctrl+Alt+N) does the same
from anywhere in the window, also while typing in the chat (``chat_toggle``).
The toggle is labelled **Chat** followed by its shortcut as bound now, as
Blender's menus print it (``Chat  ⌃⌥N`` on macOS, ``Chat  Ctrl Alt N`` elsewhere;
``shortcuts.text``, ``KeyMapItem.to_string``; just *Chat* when the shortcut was
removed). It is pressed while the chat is shown, its icon is a speech bubble,
filled while shown and outlined while hidden, and its tooltip says what a click
does (*Show the Angelo chat* / *Hide the Angelo chat*). History: 1.0.2's bare
icon went unnoticed, 1.0.3 added the label and the hint, and 1.0.4 put the
shortcut on the button and drew it in Director too, after a user still could not
find how to show the chat in Manual mode (2026-10-21).

A one-time hint says where it is (``chat_hint``, since 1.0.3). It is a kit card
standing right under the toggle, with a small arrow pointing up at it, in
whichever editor lies under it (the chat, the Outliner, the 3D view). The toggle
is the first item of the top bar's right-hand region, which Blender sizes to its
content, at a measured width (``top_bar_fit``, as Studio, Flows and the mode
menu are, so no language clips a label), so its rectangle
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

* Hide undoes Show, after keeping the draft (``chat_hide``): a chat Show made as
  a new column beside Properties gives its strip back to the editors it came
  from (the dock's own inverse, ``docking.collapse_chat``); any other chat, or
  one whose layout changed since, closes with Blender's own Close Area, so its
  neighbours take the room. Before 1.0.5 every Hide was Close Area, so each Show
  and Hide moved the chat's width from the 3D view to Properties (shipped 1.0.4,
  Layout tab: 3D view 1881, 1286, 686, 356 px over three cycles). A popped-out
  chat's window closes. The conversation never changes.
* Show docks the chat on its last side (``ui.request_chat_dock``) with that
  draft. Window > Angelo Chat still opens it too. The room for a new column
  beside Properties is counted in the editors that border that column, the ones
  the dock takes the strip from (``layout.neighbour_room``); when they can't give
  the chat its width but the column can give more (Blender's own Close Area on
  the chat can hand its room to the Properties/Outliner column), the chat takes
  its width from that column instead of failing (``layout.plan_dock``). Its layout
  checks (a column spanning the window, editors on one edge) allow for the
  space Blender leaves between editors as it is now: its padding grows with the
  interface scale and Preferences > Interface > Border Width
  (``layout.border_gap``, ``docking.gap``). Before 1.0.5 a fixed 4 px refused
  every dock at UI scale 2 ("The outer editor column must span the window
  height"). A Show that still can't dock says why on a corner card (*The chat
  couldn't open*), since the hidden chat can't show its own error. A dock is
  done when the chat has drawn as the chat, not when its area exists
  (``chat_paint``): the move holds until that area's own draw after the dock's
  last step (at most 2 s; in practice one frame), and only then does corner
  Angelo go and the caller hear of it. The chat itself was always drawn in the
  first frame after the dock. A GUI capture that forces two buffer swaps
  (``wm.redraw_timer`` DRAW_WIN_SWAP) and then reads Blender's front buffer can
  return the window from mid-dock on macOS (a second Properties editor where the
  chat is), so the suites judge the chat by its region's own pixels, read in its
  draw, and retake a picture until it is current
  (``test_blender_chat_launcher``).
* An Angelo corner card (the update notice, this hint) never makes docking or a
  mode change wait (``input_policy.layout_blocking``); a modal card, a real tool
  and Blender's window screenshot still do. Before 1.0.3 the hint's own Show
  chat, and Show or Director beside an update notice, refused or waited.
* In Director, Hide closes the chat's area and the 3D view takes the window;
  Show rebuilds Director's own split (``director.request_mode``), the chat on the
  right at Director's width with its draft. Entering Director always shows the
  chat. Window > Angelo Chat does the same as Show in Director. Before 1.0.4
  Director drew no toggle and the shortcut only said to switch to Manual mode.
* **Angelo in the corner** (``chat_launcher``, since 1.0.4, the maintainer's request): while the chat is
  hidden, in either mode, the raven of the chat's marble wait sits in a bottom corner of the window's largest
  3D view, at the marble's compact hero size (``marble_wait.HERO_COMPACT``, 56 px at UI scale 1) on the Angelo
  button's round card (``round_button``: 76 px, shadow, card fill, edge ring; teal ring on hover), drawn from the
  marble's own cached batches (``marble_draw.draw_still``). It stands on the
  chat's side (Director's chat is on the right), clear of the toolbar, the sidebar and a bottom asset shelf
  or header drawn over the view; the navigation gizmo is at the top and the status bar outside the view. The
  pointer over it lifts its eyes, rings the disc in teal and shows the toggle's label as a tooltip (*Chat ⌃ ⌥ N*).
  A click, or Return with the pointer on it, shows the chat after the event and Angelo goes at once; it comes
  back whenever the chat is hidden, however (the toggle, the shortcut, Close Area, a pop-out closing, a tab
  without the chat). Its input is a passive canvas handler (``input_policy.CANVAS_INPUTS``): it takes only left
  and right presses on its own disc, nothing while a real tool holds the window, and never makes a tool, a dock
  or a mode change wait. It blinks every few seconds only in a 3D view whose own draw is cheap (a blink is three
  redraws of the view; the view's cost is measured between its PRE_VIEW and POST_PIXEL callbacks), never with
  Reduce Motion (Blender's or the system's). Its own draw is a disc, a ring and the raven's batches. It moves the
  moment the chat's state changes where something says so: a toggle, a workspace switch, a mode switch or a
  dock ending or failing, a file load, and an area's editor type or a console's language changing (the Editor
  Type menu, Shift+F keys, a script; Blender's message bus). A click's wait for the chat ends when its request
  does (shown, refused or failed), not after a timeout. Only what notifies no one waits for the twice-a-second
  watch: Blender's own Close Area, a window closing or opening, Maximize Area, and a resize that makes another
  3D view the largest; its draw never stands beside a shown chat meanwhile. Not in
  background or automation runs unless ``ANGELO_ENABLE_CHAT_LAUNCHER=1``. Keyboard: the shortcut on its tooltip
  (the 3D view has no keyboard focus to tab to). ``test_native_chat_launcher`` and ``test_blender_chat_launcher``.
* **The Sessions panel minimized** (``rail_minimized``, 1.0.6, the maintainer's request): minimized, the parallel-edit
  Sessions panel is the same round card as corner Angelo (``round_button``, one drawing for both) at the top right
  of the 3D view, holding Angelo's robot (``rail_glyph``), the number of workers on a teal badge (Main excluded,
  queued parts included) and an amber dot while a worker waits for approval. It stands below the headers drawn over
  the view, left of the sidebar, right of the toolbar, above a bottom shelf, and never on Blender's navigation gizmo:
  ``gizmo_rect`` places the axes and the zoom, pan, camera and perspective column as Blender 5.2's
  ``view3d_gizmo_navigate.cc`` does (the visible rect after overlapping regions, ``UI_SCALE_FAC``, the Navigate
  preferences, the minimal axes, quad view's top-right quarter, a camera view's or a locked view's column). The
  circle goes left of the gizmo in the top row while it stays in the view's right half, else below it at the right
  edge, else (a view narrower and shorter than the gizmo and the circle together, such as 1280x720 at UI scale 2 with
  the chat docked and the sidebar open) where it covers the least of it. The pointer over it rings it in teal and its
  tooltip says *Show the Sessions panel*, the workers' count and a waiting approval; a click, or Return with the
  pointer on it, shows the panel again (kept in the person's preferences, as Workers at once). Its input is the
  session rail's handler's (``rail_minimized.handle``). ``test_native_rail_minimized``.
* **The chat follows workspace tabs in Manual mode** (``chat_toggle.follow``; **off in 1.0.4**,
  ``FOLLOW_TABS``: a followed chat was sometimes not painted until the pointer moved over it, so it waits for
  1.0.4.x; meanwhile a tab without the chat shows Angelo in its corner):
  switching tabs (Layout to Shading, a click, Ctrl+PageUp or a script; the
  message bus on ``Window.workspace`` runs it at once, ``_watch`` twice a second
  as a fallback) docks the chat in the new tab on its side, once that tab has drawn (a dock made
  inside the switch, before Blender laid the shown screen out, left the chat unpainted until the pointer crossed
  it; ``_dock_when_drawn``, at most ``SETTLE`` seconds later), when the person wants
  it and the tab has a 3D view. Wanting it is their last choice
  (``follow_step``, host-tested): a chat seen docked or popped out means yes; a
  chat that disappears while the tab and mode stay the same (the toggle, the
  shortcut, Close Area, a pop-out closing) means no. A tab without a 3D view
  (Compositing) is left alone and the choice carries to the next tab. A tab whose
  own layout still holds a chat shows it. Mode changes place the chat themselves
  (``director_layout``). Before 1.0.4 the chat lived in one tab's layout, so a tab
  switch made it vanish (and fired the hidden hint); the hint now judges only the
  result of a follow (``_watch`` skips it while the chat is moving).

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
  model to start* (Choose a model, which opens the model list). While the chat's
  own connection's models load, the card says loading and never asks for a
  choice. A New Chat (``ui.create_session``) inherits the current chat's
  connection, or the only ready one, together with the models Angelo already
  has for it (``chat_start_states.bind_models``; it loads them when it has none), so a new
  chat on a connection with models shows *Default*, never *Choose a model to
  start*. The model chip and the
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
* **Auto-save runs with the chat open.** Blender's auto-save waits while any
  modal operator runs, and the chat's input handler runs in every window that
  shows the chat, so up to 1.0.5 Angelo never auto-saved while the chat was
  open. The canvas inputs and passive overlays carry the native
  ``MODAL_PASSIVE`` operator option (``OPTYPE_MODAL_PASSIVE``), which auto-save
  ignores; each takes it from its classification
  (``input_policy.modal_options(bl_idname)``), never by hand. Any other modal
  operator, Angelo's holding tools included, still makes auto-save wait and
  retry every 10 ms until it ends, as in stock Blender
  (``test_blender_autosave_passive_modals``).
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

Usage meter
===========

A bar along the top of the chat (``chat_usage``) stays put while the transcript
scrolls under it. A new chat shows it as soon as a connection is selected, before
anything is sent, so the plan's window is in view while the person decides
whether to send: "0 tokens" with, on a plan, the busiest window the runtime has
reported for that connection (ring and text), or on an API key or team "≈ $0.00"
with the *Estimate* label (``chat_usage.fresh``: no message from the person or
the agent yet; host notices do not count). Any other chat shows it from the
moment its first counted request starts (a chat from before the meter shows it
from its next request; earlier requests were never counted, so it never claims a
total for them). ``test_blender_chat_usage`` reads the drawn bar in a fresh chat
on a stand-in plan, API-key and team connection. It shows the chat's total tokens
(cache reads included), how much of their input was cached ("93% cached":
cache-read tokens divided by all input tokens, that is cache reads, cache
writes and uncached input) and, beside them, the estimated spend on an API key or a
team connection (``≈ $0.42`` with an *Estimate* label), or on a Claude or
ChatGPT plan the plan's busiest window as a ring and text ("5-hour limit: 34%
used", amber from 80% or when the plan refused a request) and "Included in your
Claude plan", never a dollar figure. While a request runs the total grows with
the runtime's running totals and ends in "…". A narrow chat drops the plan's
name first, then the Estimate label, then the window text, then the cached
share; the tokens and a
dollar figure (with its "≈") always stay. Clicking the bar opens a card hanging
from it (popover ``"usage"``) with two scopes, **This chat** and **Since Angelo
started**: the token split (cache reads, cache writes, uncached input, output)
with the cached share and a one-line definition of it, one row per connection
and model, the chat's last request (its tokens, cached share and, when paid,
estimate), the plan's windows with their reset times, and what the numbers
rest on.

* **The running turn's tokens.** ``usage_meter.turn_tokens(controller)`` is the
  output (thinking included) of the request in progress: what the runtime has
  reported, plus four characters a token for what streamed since its last report
  (Claude's ``RoundStream`` reports every delta: text, plan JSON, visible
  thinking; Codex's text and reasoning deltas are counted where the controller
  receives them). Each report settles the estimate and the receipt is the final
  count; hidden thinking streams nothing, so the count holds still there. The
  live row shows it beside its phase and clock (``usage_view.turn_label``); the
  row itself belongs to "Transcript activity". Display only: never in a receipt,
  the ledger or anything a model reads.
* **Where the numbers come from** (``usage_meter``). A finished request's
  receipt (``telemetry``): the tokens every provider turn reported, the
  connection mode, the served model and the session's cache TTL, plus its
  read-only consultations. Codex warm-ups count since Angelo started, never in a
  chat. Per-chat totals are kept in ``usage.json`` in the state folder, written
  on a background thread and bounded to 1,000 chats and to the reader's own
  1 MiB (the oldest chats are left out of the file first), so a file it writes
  always reads back; a larger file an earlier build wrote is read once and
  trimmed. A file that can't be read now, or another version's, is never
  written over: the totals stay in memory and a later write adds what it holds.
  The chat file itself is unchanged, so an older build still opens it. The
  totals since Angelo started are memory only.
* **The estimate.** Tokens times ``model_profiles.rates`` for the model that
  served the request, with cache writes at the session's TTL rate and the
  per-request prompt size for stepped prices. A Claude turn served by several
  models is priced at Claude Code's own list-price figure. A model without a
  published price is counted in tokens and named as not priced, never as $0; a
  request a runtime did not fully report (a stopped turn) makes the totals a
  lower bound ("At least"). Every money figure says Estimate and that the bill
  comes from the provider (or, on a team connection, the organization's
  providers) and can differ. This is not the team console's metering, which the
  gateway does on its side.
* **Plan windows** (``usage_windows``). Kept per connection from what the
  runtime sends with its replies, never requested: Claude Code's
  ``rate_limit_event`` (2.1.292 and later: the limiting window's utilization and
  reset, and the 5-hour and weekly windows when the responses carry them, and
  whether extra usage is in use) and Codex's ``account/rateLimits/updated``
  (primary and secondary windows, sparse updates merged). A window whose reset
  time passed is dropped until the next reply reports it; the card says how old
  the report is. API-key sessions receive none.
* **Cost on the chat path.** None of this runs before a request is sent. A
  finished request adds its receipt in memory (``usage_meter.record``; the file
  write is on the background thread, drained when a chat closes). The bar's parts
  are recomputed only when their inputs change; the card only while it is open.
  Any failure leaves the bar out and the chat draws as before.
* **Worker chats write no usage** (``worker_usage``, design A′). In a worker
  Blender, the telemetry store, the usage ledger, the team task count and the
  plan windows forward each record to Angelo as one ``usage`` or
  ``telemetry`` event, and write no file. The worker still keeps the record in
  memory for its own reads. Angelo writes each record once, keyed by its id,
  through the same stores and in its own state folder, never one the worker
  names. So the per-connection windows and the Team reports have one writer
  and stay whole. A record that cannot be sent is dropped, never written in the
  worker. A chat whose stores do not forward is never hosted
  (``worker_usage.covered``). Pinned by ``test_native_worker_usage``.
  ``test_blender_chat_usage`` measures the chat's own draw time with the bar
  shown and hidden (``performance.rst``, "Usage meter").
* Tests: ``test_native_usage_meter`` (estimates, ledger, windows, wording),
  ``test_native_usage_meter_hooks`` (the controller's receipt and both runtimes'
  window events, the live count), ``test_native_chat_usage`` (cached bar, what a
  narrow bar keeps, card blocks and placement) and the GUI fixture
  ``test_blender_chat_usage`` (the bar and card in API-key, plan and Arabic
  layouts, real modal clicks, draw cost).

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
  interrupt a running script. A parallel worker's chat inherits the lead's
  Trust scripts for this chat in Auto, and in Ask every worker script asks
  (``agent_execution.rst``, "Worker chats"). A card's Approve, Review and
  Decline act on the chat whose card it is (``session_id``), never only on the
  chat shown now.
  File writes and deletions need individual approval in every mode. Applying a
  scene change requires Blender's Global Undo. Tool calls dispatch to a bounded
  main-thread queue; ``agent_execution.rst`` specifies the tools.
* Approval cards describe the change in plain language, naming objects rather
  than handles, with the exact code or parameters under Show code or Show
  details. A card never claims more than the step does: a script's body reads
  "The agent will run this Blender Python code. Review it before approving."
  whatever the script does (the model's own description is the title), and a
  tool the card has no wording for asks "Approve this step". The card's single Approve button never looks disabled; for an
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
kit's status chip (Running, Done, Failed, Declined, Stopped, Needs you,
Preview, Reverted). A step whose card the person declined or that a Stop ended
(``DECLINED``, ``CANCELLED``) reads Declined or Stopped, never Failed, and the
summary says so ("Looked at 1 thing · 1 step declined"), in lead and worker
chats alike: it was the person's choice, and it did nothing to count. The
titles, kinds and chip states come from ``activity_text``: an unknown tool or
error code falls back to a generic phrase, never its name, and the Claude
runtime's plan call (``StructuredOutput``) reads "Planning the changes" while
it is written, then "Planned the changes". A title reads true for its state:
every tool's title has three forms (``chat_titles``, ``activity_text.EXTRA``
and the generic phrases by kind): the past for a step that happened ("Ran
Blender Python"), the progressive for one under way, a running step or a
generation still in flight ("Running Blender Python", "Generating media"),
and the action for one that did not happen, queued, waiting for approval,
declined, stopped or failed ("Run Blender Python" beside its chip); a noun
phrase ("Scene edit") is one entry for all three. ``chat_titles.happened``
(completed without an error) and ``in_progress`` (running) decide. A planned
step and a call under review read as actions. A read that did not happen says
"Inspect", "Check" or "Open", because "Read" is past and present alike in
English but not in translation. ``chat_titles.tool_title`` (the console, the
changes popup) adds the state as a suffix ("· declined", "· stopped"), except
"running" beside a progressive title, which already says it; a tool it has no
title for reads the generic phrase of its kind, never its id.
Host-verified results, script and plan failures and restore outcomes draw as
the kit's inline notice: a friendly headline (an error code or exception class
becomes a sentence) and one suggestion, with the stored text, ids and
exceptions under a collapsed Details; a plan that simply completed is one
muted line. All of it is display only: stored rows and everything the model
reads are unchanged.

Tool rows expand to show the tool, its arguments or code, the actual result and
errors (a plan call shows its operations, never its reply). Thinking rows show only reasoning text the runtime supplies
(or, without any, how long the model worked: "While a turn runs" below); while the
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

Every tool's change report follows one schema (``chat_changes.report_problems``):
``added`` and ``removed`` hold ``{name, type}`` records and ``modified`` holds
``{name, fields}``, all three as lists in any report that names one of them;
``truncated`` is a bool, and ``file_changes`` lists its ``written`` and
``deleted`` paths. ``controller.finish_tool`` checks every result against it.
In the native suites (``ANGELO_STRICT_CHANGE_REPORTS``; not LIVE suites,
which run as shipped) a drifting report adds the error to the tool's result
and keeps everything else in it. In a person's chat the report is read
normalized: a group it lacks is empty, and an entry it can't read is still a
row. No single row, card or report stops the chat from drawing
(``chat_guard``). The transcript's rows and the cards after it (approvals,
the live row, preview and job cards) are built, counted and drawn one by
one. One that raises is drawn plainly: who wrote it and up to three lines of
what it says, over its own rectangle, with its clicks dropped and the drawing
state restored. An approval falls back to a plain card that says what the
real card says before its Details (``approval_review.preview_text``: the
title, the body with its usage label, and the warning). It keeps Decline, and
keeps Approve only when those words were built and approving needs nothing
else the real card holds. A script's code, a reviewed program and a flow
run's selection do need the real card, so a plain card offers Decline alone
for them; a card that fails while drawing keeps the same answer. Each kind of failure is logged once, and each draw's failures become
``chat_view.last_error``, so the GUI suites that check it still fail. The
Changes popup does the same per report.

While Claude Code streams tool input, a Claude plan's reply is drawn in its
chat row as the model writes it, and kept only when the plan arrives (a plan
that never ran leaves nothing), and a script being written is named in the
live row by its own description (``agent_execution.rst``, "How long a turn
may run"). In 1.0.4 the well below ships off (``script_overlay.SHOWN``): the
A/B that would ship it did not pass its pre-registered rule. With it on, the
code shows as it is written, in a well under the live
row's steps (``script_stream``, ``script_overlay``): a caption with the line
count so far ("Writing the script: 142 lines") and the newest five lines in
the code font, the line being written brightest with a teal caret. The well
is five lines tall from its first line, so the row never grows line by line;
it updates at most twice a second, like thinking, and leaves as soon as the
code is complete (the script is written; its "Planned" step stays while the
plan's later steps and reply are written) or with the plan's status (the plan
arrived, the runtime refused it, a later call started over, Stop, the turn
ended). Lone surrogates in the code are drawn as U+FFFD. The lines are redacted whole each time they are drawn,
holding back a tail that could begin the connection's key, and are never
stored, saved, handed off or sent to a model; the script that is reviewed and
runs is the complete call. The recorded Opus 5.5 footbridge builds of the
1.0.0 scene campaign wrote their script for 35-44 s after the plan started,
the longest still stretch of a one-shot after the model's thinking.

On the opt-in direct route (``claude_direct``) an Angelo edit tool's call shows
its provisional step as its input streams, as a plan's steps do, and an
``angelo_execute_python`` call its code in the same well; the call's end takes
them away and the host draws the call's own row when it runs it. A lookup
shows nothing until it runs, milliseconds later. Codex delivers each tool call
whole (``item/tool/call``), so a Codex script cannot show before it arrives;
its reasoning summaries and a typed plan's steps already stream.

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
  row's raven is cocked 7° and blinks every 3.8 s while steps run, beside the
  turn's clock ("While a turn runs" below). Nothing is timed to look like
  progress.
* **Thinking summaries.** A GPT summary names the live row by its title
  (``summary_title``). A Claude thinking block without titles names it by its
  latest finished sentence (``summary_line``, at most 64 characters, cut at a
  space): the hook is in place for runtimes that stream summary text, and
  inert while Claude Code omits it, as it does for Opus 5.5 unless asked.
  Only debug mode asks for it: ``debug_thinking.SHOWN_TO_EVERYONE`` is off
  for 1.0.4, because the whole-task Opus A/B measured +11-14% cost and +8-10%
  wall (``performance.rst`` "Streamed progress"). Either way the
  line is the runtime's own text, drawn as data, and a new line is a tap.
* **Reduce Motion.** Blender's Reduce Motion preference, or the system's
  (macOS Reduce motion, Windows "Show animations" off, ``motion_preference``),
  asked once per turn: chips fade where they stand (no flight, dust or shake),
  the raven does not blink or tilt in motion, and the hero's size changes in
  one step.
* **Cost.** ``marble_host``'s timer exists only while the drawn chat runs a
  turn (started by the chat's existing 0.1 s poll) and asks for a frame only
  while something moves, at most 12 a second. Its 24 Hz scheduling granularity
  remains for the clock and transitions. A frame does not lay the chat out
  again: while a wait runs the chat's full draw is kept as a texture
  (``marble_frames``), and a frame the timer asked for draws that texture and
  the two marks over it. Costly own draws slow the cadence further; the native
  GUI gate checks the 2% whole-frame CPU budget, including composition that
  the own-draw timer cannot measure. The animation pauses while the person orbits,
  scrolls a view, drags or runs any other modal tool, during playback, when the
  window is out of focus and when no mark is in view. It reads the controller
  and never writes it: the provider threads, the event pump and native dispatch
  are untouched. The measured cost and the ratchet operations are in
  ``performance.rst`` ("Chat waiting animation").

``test_native_marble_waiting`` covers the geometry, schedule, events, reveal and
frame scheduling without Blender; ``test_blender_chat_waiting`` runs a real turn
through the stand-in Claude CLI and checks state, frames and pixels at both
sizes, before and after the plan arrives, and with Reduce Motion.

While a turn runs: phase, clock and silence
============================================

A long think must never look like a stuck one (a user asked for exactly that). The live row's header
(``live_progress``, drawn by ``chat_rows``) always says three things while a turn runs:

* **Phase.** "Waiting for the model" until the API's first event for the turn (Claude ``message_start``, Codex input
  accepted); then the status's label ("Thinking", "Preparing the next steps", "Provider busy, retrying…" for
  Codex resends and Claude Code's ``api_retry``). While a step runs, the host's work: "Working in Angelo",
  "Searching the web", "Consulting another model" or "Generating media". Host statuses never read "Thinking"
  ("Staging native program" reads "Working in Angelo"). An approval replaces the live row with its card.
* **Status line.** After the raven, the line reads like a coding agent's status line: "• 1m 9s · 562 tokens ·
  Thinking", a breathing teal dot, the turn's elapsed time, its output tokens so far (``usage_meter`` owns the count
  and its words; no count, no token part) and the phase. The line is a marble mark (``marble_draw.paint``): drawn over
  the kept picture, so a tick costs one cheap frame, not a layout. The timer asks for a frame each new second, which
  still comes while the window is out of focus (someone watching from another app sees the time move), and for the
  dot at 8 frames a second while nothing pauses the wait (none with Reduce Motion). A modal tool, view navigation,
  playback or no mark in view pause it like the raven. A painter that fails loses only its own frame. A blocking script on the main thread cannot be
  drawn over: the row shows "Working in Angelo" before it and the time catches up after it. When the turn ends,
  a muted "Worked for 1m 58s" stays under it (``worked_ms`` on the request row, never sent to a model).
* **Silence, said honestly.** Providers report signs of life (``turn_activity.signs``: accepted, the last model
  event, the last event of any kind, keep-alives included, ``TurnActivity.heard``). Only a model round in progress is
  judged: between rounds, while a step runs or while the runtime relaunches, the host is the one working and no note
  shows. A thinking row with no summary text for 20 s adds a muted line: the model is working without sharing its
  thoughts. A stall needs evidence: a runtime that sends keep-alives (Claude Code) has sent nothing at all for 180 s,
  or one that sends none (Codex) is silent for 180 s with no reasoning item open, since an unsummarized reasoning
  item is a quiet model, never proof of a dead connection. Then the dot turns amber and the line says for how long,
  that it may still answer and that Stop ends the turn; anything arriving clears it. The clock belongs to the
  request that started it (``Controller._begin_metrics`` stores it with the request's generation), so an External
  app's read or work never shows an earlier turn's time. The host's own give-up after 10 minutes of silence is
  unchanged.

A thinking row opens to the runtime's summary. When the runtime sent none (Claude Code without summarized thinking
asks the API for brief updates only; Codex requests summaries, and the model leaves many reasoning items without
one), it says how long the model worked and what it led to, one whole translatable sentence per outcome ("The model worked
for 2m 10s without sharing its thoughts, then wrote a plan with 8 steps."). A home folder in any of this text reads
"~" (``live_progress.personal``: macOS, Linux and Windows forms, names with spaces, percent-encoded, under a mount,
``~user`` and ``/root``; web addresses are left whole). All of it is display only: nothing is
stored, sent to a model or changes what runs. ``test_native_live_progress`` covers the rules without Blender;
``test_blender_chat_waiting`` checks the clock in the running app.

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

The + menu
==========

The chat input bar is Angelo's main navigation, in Director and in Manual (1.0.5). Its **+** opens one menu built
from a registry (``chat_destinations``): a feature adds a ``Feature`` at its module's ``register`` instead of a
button somewhere else, and ``chat_add_menu`` draws whatever is registered, in ``order``, under its ``section``
heading. Shipped (``TOP_LEVEL``): **Attach**; **Media Studio** (opens it in its own window, ``studio_toggle``);
**Generation Flows**, a group with New Flow, the templates (``flow_templates``, the same list every entry point
shows) and the person's flows (``flow_open``); **Workflows** (Media Studio on its Workflows tab, such as Character in
parts: ``angelo.open_media_studio`` WORKFLOW, ``studio_toggle``); **Base meshes**; **Work on hands**, one row that
one click chooses (``hand_rigging.rst``, "Choosing the hand tools").
A worker's chat lists neither tool (``Feature.lead_only``): Send performs chosen tools on Angelo's own scene only
(``console.execute``), and a worker's chat works on its worker's document, so a chip there would do nothing.
A feature is one row that runs an operator, or a group: a header that opens and closes, closed until the person
opens it, with its rows indented under it while open. The open groups are remembered in ``chat-add-menu.json``
beside the chat's local state (a damaged file closes every group), so a long list (21 base meshes, every saved
flow) never buries the rest. ``test_native_chat_destinations`` checks every ``TOP_LEVEL`` feature is registered by
its module and shown, and that a module constructing a ``Feature`` lists it there.

The menu grows with its chat: as wide as the column allows up to ``MAX_WIDTH`` (480), and as tall as ``COVER`` (70%)
of the chat, never under 1.0.4's 520, before its list scrolls; so the rows 1.0.5 added take none of the base
meshes' room, and the transcript always shows above it. A row's why line wraps to a second line (the row grows)
before it is elided, and opening the menu scrolls the chosen row into view. ``test_blender_chosen_tools`` checks
six base meshes in view, every drawn why line whole, and the chosen mesh in view when the menu reopens.

Start from a base mesh
======================

The composer's **+** menu's **Base meshes** group (closed until opened): **Attach** (the file
browser, as before) stays above it, and the group holds one row for each of the 21 bundled Human Base Meshes
(``human_base_meshes``; heads first, each with its height and vertex count). With a mesh selected, an opt-in
toggle, "Match the size of <name>", fits the pick to that object's centre and height; otherwise it stands at
the 3D cursor at real size. The menu scrolls, takes the arrow keys and Return, and closes on Escape or a click
outside. A pick becomes a teal chip beside the attachments, and its **×** removes it.

At Send, before the observation is built, ``chosen_tools_host`` places the pick with ``asset_start.place``. That
is the operation a model's typed create will share: it appends every object the asset holds, removes the file's
collections, tags each object ``angelo_base_mesh`` and leaves the main object selected and active, all in one
Undo step ("Start from a base mesh"). One chat row says what was added:
"Added Head – Stylized to start from", the name the menu and chip show. No row a person reads names an asset's
object or data-block ("GEO-head_stylized") or a timing; timings stay in ``last_chosen_timings`` for fixtures. The
fit toggle names a selected base mesh Angelo placed by its menu name (``chosen_tools_host.shown_name``), any other
object as the Outliner shows it. ``test_blender_chosen_tools`` and ``test_blender_chat_start_states`` fail on any
drawn + menu or chat row that reads as an internal name. The bundle misspells three
asset names ("Primitve"): ``name``, what people read, is corrected, while ``datablock_name`` keeps the upstream
spelling the append looks up, and a lookup accepts either. The model learns of
it from the scene facts it already receives (the new objects in the observation); nothing in its instructions,
schemas, budgets or observation fields changes. A pick that fails removes what it
added and says why, and the message still goes. The step runs before the request checkpoint, so restoring
that checkpoint keeps the mesh; its own Undo step removes it.

Tests: ``test_native_chosen_tools`` (rows, choices, layout, keys) and ``test_blender_chosen_tools`` (GUI: the
menu as drawn with the group closed, then opened, scrolling, the chip, the fit, the Send step, every base mesh timed, the staged-context path, a
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
snapshot and opens the snapshot with embedded scripts disabled. The window
keeps showing the document that was open at the Restore (``stool.blend``, never
Angelo's private working copy), and nothing is written over that file until the
person saves and confirms: the first Save over it asks once, "Save the restored
scene over "…/project/stool.blend"?", any other save of it is refused until then,
and the snapshot from before the restore stays available through Undo restore
(``checkpoint_policy.rst``, "The document after a Restore"). Chat history
survives, and journaled file-tool and Studio-export changes since that point are
reverted. Arbitrary approved Python, manual exports and network
effects are not recoverable. The Restore dialog says this plainly, in three
short paragraphs after its question: every chat shares the scene and keeps its
history, a snapshot of the scene as it is now is saved first so Undo restore can
bring it back, file-tool changes are put back but a script's changes outside the
scene and anything sent online are not, restoring never saves the scene over the
project file, and the unsaved image pixel rule (``checkpoint_images.draw_restore``).
Policies, deduplicated storage and retention (2 GiB by default) are in
``checkpoint_policy.rst``.

A row's words for the model and for the person can differ: ``ui_text.model_notice``
keeps the model's English instruction as the row's ``model_text``, which the
handoff, connected apps and a lead's part status read (``ui_text.model_text``),
while the chat shows the person's translated notice. When a chat loads after a
restore that did not finish (its notice still ``pending`` or ``partial``), it
starts a fresh native context and adds one such row: the model reads that the
outcome is uncertain and nothing may be repeated automatically; the person reads
"A restore in this chat didn't fully finish" with what to check
(``session_validation.recovery_check``). A later load adds no second row, and a
row an earlier build stored as the model's instruction alone becomes this row.

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

These need no login, inference, Fast request or microphone (the usage meter's
``test_blender_chat_usage`` among them: its receipts and plan windows are
fixture data). The LIVE suite ``test_blender_chat_usage_live`` makes two short
read-only turns on one Claude subscription and runs only on a dedicated
connection store, named by ``ANGELO_QA_USAGE_CONNECTIONS`` and also the app's
``ANGELO_CONNECTIONS_DIR`` (``eval_live_agent.isolated_connection`` makes one:
that connection's metadata, never its credentials); it refuses any other store,
so it never bills the developer's own sign-in. The other LIVE suites
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
