.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Generative AI / Media Studio
============================

The editor picker has a separate **AI** column: **Coding Agent**, followed by
**Generative AI**. Python Console remains in Scripting. All three editors have
separate input state. The compiled fork exposes both AI editor subtypes natively;
the installed stock Blender can preview the same GPU canvas via the host addon.

The Studio uses Blender GPU/BLF and native operators, not an embedded browser,
MCP or screenshots-as-controls. Chat orchestrates the same direct API workers;
the coding model does not generate the images or meshes itself. It has only Image and 3D
tabs. Create, Folders, and the asset grid occupy non-overlapping columns.
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
controls workspace presentation, not an individual chat. Scene remains a scope
selector in Director mode, without New/Delete or view-layer editing controls.
Switching scenes retains the existing document-bound chat guard; start a new
chat before issuing mutations against a different scene.

Director hides the status bar, so the top bar carries what it would show, beside
"Angelo · Director Mode" (``director.draw_director_status``): the report banner
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
takes the menus over when the add-on registers; they draw Blender's own menu only
when Angelo's startup will not run (``ANGELO_DISABLE_DEFAULT_CHAT``, or a start
that ended without the add-on). A person who turns the add-on off gets Blender's
own menus back. Director's startup switch closes the splash (setting a workspace
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
setup card has closed and Director has settled: a banner, what Director and the standard editor are,
how to switch (the Director / Manual menu and the shortcut), demo tiles
(``first_run.DEMOS``) and Start creating, plus a button that tries the standard
editor. The chat beside it stays usable. Over the 3D view it takes the person's
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
Finder or the command line, Preferences > Themes and the Look control in English and
Arabic for Blender's form (its splash menus' draws, and the form's selected radio
button in the splash's band), with a control run that must find it. The banner art is generated by
``release/branding/angelo/welcome_banner.py`` from the raven master.
``Ctrl+Alt+D`` (Control+Option+D on macOS) is a recovery shortcut. Layout changes
are deferred and wait for foreground native modal tools. Director adds no input
handler: every viewport navigation (middle mouse, wheel, trackpad, numpad, Home,
frame selected, the view pie, NDOF), selection, transform and menu shortcut
reaches Blender natively. The chat keeps keyboard focus like a text field, so
typed letters stay in a focused chat even with the pointer over the 3D view,
while view-navigation keys always follow the pointer; orbiting, scrolling or
clicking elsewhere moves focus there (``input_policy.py``).
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
The expanded editor only occupies the Generative AI area, never the 3D viewport.

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

This opens the Generative AI editor in the current workspace, with Coding Agent
docked outside Properties on the right. It creates no workspace/tab and does not
reset connections or generate any assets on startup. Normal Angelo GUI startup
opens Director preview and Coding Agent on the right, or the remembered Manual
layout, without needing a preview script. Explicit Studio preview launch returns
to Manual mode first. Background
runs skip that setup; test automation can opt out with
``ANGELO_DISABLE_DEFAULT_CHAT=1``.
