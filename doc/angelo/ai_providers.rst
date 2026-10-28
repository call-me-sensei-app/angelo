.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Angelo AI provider connections
******************************

Connection management is separate from the image and 3D generation UI. No
provider is selected globally or used as a billing fallback.

Groups and credential boundaries
================================

The page is drawn on Angelo's canvas (``providers_page_*.py``), not with stock Blender widgets: one centred
column (at most 720 px) of a hero with **Add connection**, an Attention list (organization locks as chips,
pre-rename folders, saved-key notices), the Angelo team bar or card, Get started (first run), Coding agents,
Media generation, Agent permissions (switches), Background jobs (switches: keep the computer awake, notify
when a long job ends; ``long_jobs.rst``), AI Enhance and External AI apps (MCP)
(``providers_page_external``; ``external_mcp.rst``). ``providers_page_model`` reads
the same owners as the stock fallback and maps each connection's status to a chip (``readiness``: Available in chat, Key
verified, Checking…, Sign-in expired, Key rejected, Needs credits, Not checked yet, Not connected);
``providers_page_layout`` lays it out (host-tested); ``providers_page_host`` draws it in the Preferences
region, leaves scrolling to Blender's View2D through an invisible spacer, and runs every control through the
same operators as the stock fallback. Its Connect rows are the chat's (see "Suggested, never set up" below) and connect
through ``angelo.quick_connect``; a connection whose provider sign-in is still open in the browser reads
Checking…, not Not connected. Without a window (background runs, fake layouts) ``ui.draw_ai_providers`` keeps
drawing the stock fallback, which lists only a sign-in already found, with the prefilled form.

Add, Edit, Replace key and the chat's "Use an API key" open the connection sheet (``providers_sheet``), a
canvas card over the page (or over the 3D view from the chat, never Preferences) with the kit's segmented
control, provider dropdown, masked key field (Paste, Show), Remember toggle and Advanced (model ID). For a
subscription, *Use existing sign-in on this computer* starts ticked only when that provider's sign-in was found
here (``native_suggestions.reuse_by_default``: status "Connected to …"); otherwise it starts unticked and the
sheet says Connect opens the provider's sign-in in the browser (the welcome card's words). A found sign-in's own
form, and a click on the box, decide instead, so the box never claims a sign-in that does not exist. It saves
through ``angelo.new_connection`` (``EXEC_DEFAULT``), then follows the connection's own status: checking,
works, or the reason with a fix. The key exists only in the sheet's field state and is wiped on submit,
cancel, close and file load. A row's ⋯ opens an anchored menu (``providers_menu``) with the stock menu's
items; Remove confirms in place before ``angelo.connection_remove`` runs.


Coding Agents contains ChatGPT/Codex and Claude subscriptions, their separate API
connections, and Grok/MiniMax/Kimi API connections. The model picker merges ready
coding accounts into searchable provider groups. It has ONE Default row scoped
to the current chat's account, not two recommended options. Selecting a model
keeps the shared conversation, its references and checkpoints. Each connection
retains its own native context. A pending choice applies only to the next turn.

Media generation contains five independent API-key services. OpenAI image billing
does not inherit a ChatGPT subscription or an OpenAI coding credential. GenAI
accounts are excluded from model discovery, chat selection and subagent
resolution, including when a catalog is accidentally seeded for that account.

Metadata schema v3 adds a non-secret native profile reference (v2 added group);
older four-mode connections migrate to Coding
Agents without replacing UUIDs or native sign-ins. No keys are serialized into
metadata, preferences, blends, logs, transcripts or receipts. BYOK keys are
remembered by default in the operating system's own store: the macOS login
Keychain, Windows Credential Manager or the Linux Secret Service keyring (see
`Saved API keys (OS key stores)`_). When that store can't be used, or the
person turns Remember off, they stay in memory and are labeled as needing
re-entry after a restart; there is no file fallback. The OpenAI coding API
mode uses its isolated native CLI OS keyring. Replacing a key
requires the account's turns/consultations to stop,
invalidates its catalogs/native contexts, and ignores late validation results.
Removing a connection erases its in-memory and saved key, not the provider's
account or native login.

Account metadata is app-wide and independent of preview/chat/checkpoint state.
On macOS it lives in ``~/Library/Application Support/Angelo/connections``; Windows
uses LocalAppData and Linux uses XDG_STATE_HOME. ``ANGELO_STATE_DIR`` never
changes that location. ``ANGELO_CONNECTIONS_DIR`` is an explicit test/account
override. New isolated native profiles also live under the persistent account
directory, not a random preview directory. Saved but disconnected accounts stay
listed; absence of a live status is not deletion.

A Claude API-key or team connection runs Claude Code as the subscription
connections do (no ``--bare``, so it keeps WebSearch and WebFetch;
``agent_chat.rst``, "Web and file tools") and is kept off the person's own
Claude sign-in three ways: its key is ``ANTHROPIC_API_KEY``, which Claude Code
always uses in ``-p`` mode; inherited ``ANTHROPIC_AUTH_TOKEN``,
``CLAUDE_CODE_OAUTH_TOKEN``, ``ANTHROPIC_PROFILE`` and the sign-in location and
host credentials an enclosing Claude Code session passes on
(``CLAUDE_SECURESTORAGE_CONFIG_DIR``, ``CLAUDE_CODE_HOST_CREDS_FILE`` and the
like) are dropped (``runtime_environment.CREDENTIALS``); and it runs in its own
``claude-profile/<connection UUID>`` folder as ``CLAUDE_CONFIG_DIR``, to which
Claude Code keys its Keychain entry and credentials file. A launch in any
other folder, or carrying any of those credential variables, is refused before
anything runs (``runtime_environment.require_angelo_profile``), so a chat with
no connection record, such as one on a key typed into the legacy per-mode key
operator, must add a connection first.

``scripts/angelo/recover_connections.py --from <old-state-directory>``
explicitly recovers old named-account metadata. It preserves UUIDs and the exact
native profile namespace; no credential file/keychain token is read or copied.
Saved API keys belong to their profile, so recovered API-key connections need
their keys entered again.
Legacy native profile folders remain in their original locations and must not
be deleted while referenced. New sign-ins use durable profile directories.
The JSON store uses a process lock, unique atomic temp files, stale-edit merging,
and reload-on-change so multiple preview instances cannot erase each other's
connections or resurrect a deliberately removed account. One malformed record
does not hide the records after it in the list.

Existing native sign-ins
========================

Existing sign-ins are discovered automatically but never set up
automatically: the person creates the connection. This covers Codex (ChatGPT)
and Claude Code, so a "ChatGPT" connection never appears in a profile nobody
set up.

* **Discovery stays.** At startup Angelo asks the official CLIs for the status
  of the computer's default sign-in (``codex login status``, ``claude auth
  status``), off the main thread and without inference. No credential is read
  or copied; only the whitelisted status text is kept.
* **Suggested, never set up.** AI Providers offers every subscription the
  chat's welcome card offers, with its words and in its order
  (``native_suggestions.connect_offers``): each one whose runtime is available
  (``first_run.runtime_available``), signed in or not, is a row (*Claude
  subscription*, *ChatGPT subscription*) under Get started, or at the top of
  Coding agents once one exists, with **Connect Claude** / **Connect ChatGPT**,
  the note *Uses the … sign-in already on this computer* or *Opens … sign-in in
  your browser*, and **Dismiss**, so a signed-out Claude Code beside a
  signed-in ChatGPT is offered too. Only the click connects (``angelo.quick_connect``,
  as in the chat): it reuses the found sign-in, or adds a connection with a
  private profile and runs the provider's own browser sign-in. Detection never
  creates a connection, binds a chat, selects a model or writes the account
  store. A found sign-in's row disappears once a connection reuses it; a
  signed-out one's once any connection of its mode exists (that row offers
  Sign in again).
* **Chats without a connection** never run on a detected sign-in. They show
  the Connect card below; sending refuses with guidance. Preferences never
  opens on its own, after a refused send or a sign-in failure. A legacy unbound
  chat adopts the store's active connection of its own mode.
* **Dismissal** is per mode and persists in ``connections.json`` under the
  existing ``ignored_native_modes`` key (same meaning, "don't offer this
  sign-in", so no store migration). Removing a connection never brings a
  dismissed suggestion back, and removing a connection that reused the sign-in
  dismisses it too. The sign-in can still be added with Add connection.
* **Isolated profiles.** A store listing every mode as ignored, as the live
  evaluation harness writes, offers and creates nothing. Native suites stub the
  status check, so the real CLIs and profiles are never consulted.
* **Connections created by earlier builds** cannot be told apart from ones a
  person added, so they and the chats bound to them are kept unchanged. Remove
  one in AI Providers if it is unwanted; that also stops it being suggested.
* **Codex API-key mode.** Angelo's older shared OpenAI API-key profile
  (``codex-profile/codex_api``) is still checked but is neither added nor
  suggested, because the form cannot reuse it; add an OpenAI API key connection.
* Suggestions do not affect billing, fallback, routing of chats bound to a
  connection, request fences or authentication ownership. Rules live in ``native_suggestions.py``
  (no ``bpy``) and ``ConnectionStore.suggests_native``/``dismiss_native``; the
  rows in ``providers_page_model.connect_rows`` (the stock fallback's in
  ``ui.draw_native_suggestions``) and ``angelo.dismiss_native_suggestion``.

Connect from the chat
=====================

An empty chat without a connection is the first-run setup (``first_run.py``,
``chat_welcome.py``, ``quick_connect.py``). It offers **Connect Claude** and
**Connect ChatGPT** for each official CLI found by the start-up status check,
**Use an API key** (the ordinary New connection form, opened as a dialog over
the chat) and a quiet link to Preferences. When no CLI is installed it says
which one to install. A click is the person's explicit choice, so discovery
still never sets anything up by itself.

The card draws one list, ``first_run.connect_options``, in which every row has
the same shape (label, note, action, value). Its ``team`` argument is the slot
for an organisation sign-in: ``team_chat.welcome_row`` passes a row such as
"No seat in <team> · Contact your admin" with its own action, handled
next to the others in ``chat_welcome.dispatch``. It is drawn first; for
enterprise users it is the default, and policy may hide the personal options.
A team row may add ``busy`` (a sign-in in progress: the card shows the row
running, with a spinner) and ``cancel`` (the action its Cancel button runs,
``welcome_team_cancel``).

Until a connection is ready the connect options are the only way to start a
conversation: the composer is drawn, because the chat bar is Angelo's main
navigation (1.0.5: its **+** menu reaches Media Studio, Generation Flows, the
base meshes and every registered feature before any account), but its message
field waits, dimmed and without a caret, and nothing types into it: over the
chat a key that would edit the field is swallowed (``input_policy.edits_field``;
the chat is a Console space, whose own keymap would otherwise insert it), and
every other key passes through (Tab reaches the connect options and Return
chooses the focused one). When a connection
becomes ready the field takes keyboard focus, with the example prompts (``first_run.suggestions()``:
four that use the default scene, then two that teach Media Studio and Generation
Flows through the chat, ``GENERATION_EXAMPLES``; every one fills the message box
and Return sends it, never a click alone, since a generation spends the person's
own generation account). Under them sits one
tip, ``first_run.TIP``, drawn in the card's link style (``_draw_link``, shared
with *More options*) and opening ``first_run.TIP_URL``, the website's
*Spend less, get more* docs section, which explains it. The tip carries no
figure and no cache window, so it stays true on every provider; the docs
section says what each claim is measured against. Beside the chat, the 3D view
shows a showcase instead of Blender's empty default scene
(``connect_hero.py``): a render from its one media table (an image entry
bundled in the add-on; a video entry shows its poster still), its caption, a
line pointing to the chat and *Explore the empty scene*.
Over the 3D view it stops the pointer and plain keys, so nothing changes
behind it; timers, window events, Blender's motionless hover refresh (a quit
waits for it), Ctrl/Cmd and F-key shortcuts pass through, and so do Tab,
Return, Space and Esc while the chat has keyboard focus. The chat treats it as
a canvas rather than a foreground tool (``input_policy.CANVAS_INPUTS``), so the
connect options stay usable beside it. It fades out when a connection is ready
and then releases its draw handler, input handler, timer and texture; while
shown it never animates or redraws on its own, and a drawing failure dismisses
it. The first-launch welcome screen covers the same 3D view and paints the
showcase beneath its scrim, so a first launch reads the setup card, the welcome
over the showcase, the showcase beside the connect card, then the scene once
connected. ``ANGELO_DISABLE_HERO=1`` turns
it off for automation (the native suite runner sets it;
``test_blender_connect_hero.py`` turns it back on). The still is rendered from a
script, ``release/branding/angelo/hero_still.py``.

Connect does what the Preferences form does, through the same owners. It waits
for a still-running status check, reuses the mode's existing connection or adds
one (reusing the computer's sign-in only when it is already signed in, otherwise
a private profile), runs the provider's own browser sign-in (``claude auth login
--claudeai`` / ``codex login``) when needed, waits for the re-check, binds the
chat with ``ui.choose_model_connection`` and waits for its model catalog. The
chat lands ready with the runtime's default model chosen, and offers example
prompts that work on the default scene. Progress and failures stay on the
button, with Cancel and a retry. A failed or cancelled sign-in removes the
never-ready connection it created.

Usage limits and connection failures in a chat get a readable headline above
the provider's original text, with the reset time when the provider names one
(``resets 4:30pm``, ``try again at 4:12 PM`` or an epoch), plus **Switch
connection** (the in-chat model picker) and one-step Connect for any other
detected provider. The stored chat row and everything sent to a model are
unchanged. The composer hides the "Choose model" pill when nothing is
connected, and the microphone wherever voice cannot work: without a
connection, off macOS, on Claude connections, or without ffmpeg/ffplay.

Finding the official CLIs, and the versions Angelo is tested with
=================================================================

Codex connections run Angelo's agent runtime wherever the app has one: the
patched Codex build it bundles, or the one ``ANGELO_AGENT_RUNTIME`` names
(``gpt_launch.rst``, "Agent runtime and prompt caching"). Everything below
applies to Claude Code, and to Codex only in a build without the runtime or
with ``ANGELO_AGENT_RUNTIME=off``.

``cli_runtime.py`` (no ``bpy``). A Finder-launched macOS app inherits the
minimal ``/usr/bin:/bin:/usr/sbin:/sbin`` PATH, which misses Homebrew,
npm-global and Node-version-manager installs of ``claude`` and ``codex``. So
``providers.find_executable`` tries, in order, and stops at the first
executable file:

1. The connection's configured *Executable* (a mistyped path is named in the
   error, not silently skipped).
2. PATH, then the ChatGPT app's bundled ``codex``.
3. Well-known directories, a bounded list that spawns nothing: ``~/.local/bin``
   (Claude's native installer), ``~/.npm-global/bin``, ``~/.volta/bin``,
   ``~/.asdf/shims``, ``~/.local/share/mise/shims``, ``~/.nodenv/shims``,
   ``~/.bun/bin``, ``~/.yarn/bin``, pnpm and Cargo homes, the prefixes named by
   ``NPM_CONFIG_PREFIX``, ``VOLTA_HOME``, ``ASDF_DATA_DIR``, ``PNPM_HOME``,
   ``BUN_INSTALL`` and ``N_PREFIX``, ``/opt/homebrew/bin``, ``/usr/local/bin``,
   ``/opt/local/bin``, and the ``bin`` of the newest six nvm and fnm Node
   versions.
4. Only when all of those miss: the PATH a login shell exports. It is probed
   once per process (the answer, even an empty one, is remembered), with ``-l``
   (the login profile, where Homebrew, Volta and asdf add their directories; not
   an interactive rc file), a minimal environment, no terminal, a sentinel-framed
   answer and a hard bound of 2 s or 16 KB, after which the shell's whole process
   group is killed. The output is only split into absolute, existing
   directories; it is never run. Windows has no such probe.

An npm package's ``#!/usr/bin/env node`` fails under that minimal PATH even once
the CLI is found, so a runtime found off PATH is launched (and signed in) with
its own directory and the known directories appended after the person's PATH
(``with_runtime_path``). A runtime found on PATH leaves the environment as it
was. When nothing is found the error says how to install the CLI (``npm install
-g @anthropic-ai/claude-code`` or ``@openai/codex``, or the Homebrew cask), to run
``which claude`` / ``which codex`` in Terminal and choose that file as the
connection's *Executable*, and which directories were searched.

**Windows** (``cli_windows.py``, ``child_process.py``). Discovery tries the
configured file, PATH, the ChatGPT app's bundled ``codex``
(``%LOCALAPPDATA%\Programs\ChatGPT`` and ``Program Files\ChatGPT``, under
``resources\codex-cli``; not yet checked against an installed ChatGPT for
Windows), the well-known folders (npm's ``%APPDATA%\npm`` or
``NPM_CONFIG_PREFIX`` itself, ``%LOCALAPPDATA%\Microsoft\WindowsApps`` and
``WinGet\Links``, Volta, pnpm, Bun, Scoop and Chocolatey shims,
``Program Files\nodejs``, the newest six fnm and nvm-windows Node versions, and
``~\.local\bin`` for Claude's native installer), then the PATH Windows saved for
the user and the machine in the registry (it holds folders installed after
Angelo started). Nothing is spawned. The error names ``npm install -g`` and
``where.exe claude`` / ``where.exe codex`` in PowerShell.

* *npm shims never run.* npm, pnpm and Yarn install a CLI as a ``.cmd`` batch
  file, and CreateProcess runs a batch file through cmd.exe, which parses every
  argument again (quotes, ``%``, ``^``, ``&``: the "BatBadBut" class); Angelo's
  arguments carry JSON. ``launch_command`` reads the script the shim names and
  starts ``node.exe <script>`` (Node beside the shim, else on PATH, the known
  folders or the saved PATH) or the ``.exe`` it names; pnpm's ``NODE_PATH`` is
  carried over and Yarn's nested shims are followed. Choosing npm's extensionless
  or ``.ps1`` script resolves through its ``.cmd``. Another batch file runs
  through ``cmd.exe /d /s /c`` only when every argument is a plain token, and is
  otherwise refused with what to choose instead.
* *No console window, whole-tree kill.* Every provider CLI, its version and
  sign-in probes, ``git`` (snapshot preferences), ``pdftotext`` and the voice
  helpers start with ``CREATE_NO_WINDOW`` (their children share the hidden
  console). A provider CLI starts suspended, joins a Job Object, then resumes, so
  ``terminate()``/``kill()`` (a closed connection, a cancelled sign-in, a probe
  timeout) end every descendant, such as the ``codex.exe`` that npm's
  ``node codex.js`` starts. A normal exit kills nothing: a browser a CLI opened
  for sign-in stays open. If Windows refuses the job, ``taskkill /T`` ends the
  tree.
* *A cancelled sign-in never closes the browser.* A browser the CLI starts
  through ShellExecute (as ``claude auth login`` and ``codex login`` do, when no
  browser was running) joins the CLI's job, so ending the job would close it with
  the person's other tabs. Sign-in and status commands therefore start with
  ``keep_gui_programs``: cancelling or timing out ends the job's console programs
  only (the CLI, ``node``, ``codex.exe`` and its login server, ``cmd``,
  ``powershell``), read from each program's PE header, and leaves windowed ones
  running; a program whose header cannot be read counts as console. The
  alternatives were narrower but do not work: ``JOB_OBJECT_LIMIT_BREAKAWAY_OK``
  lets out only children started with ``CREATE_BREAKAWAY_FROM_JOB``, which
  ShellExecute never asks for; silent breakaway would also let out npm's
  ``codex.exe``; ending only the CLI would leave that ``codex.exe`` (and its local
  login server) running. The cost: a windowed helper a sign-in CLI might start
  itself would also be left running (none of the official CLIs starts one).
  Every other launch still ends its whole job. Without a job, a sign-in ends only
  the CLI itself.
* *UTF-8.* Text pipes to and from every child are UTF-8 (undecodable bytes
  replaced), never the ANSI code page.
* *Git Bash.* Claude Code on Windows runs its commands through Git Bash. Angelo
  finds it as Claude Code does (``CLAUDE_CODE_GIT_BASH_PATH``, then the ``git`` on
  PATH) and in Git's standard folders; when only Angelo's search found it, the
  child environment names it in ``CLAUDE_CODE_GIT_BASH_PATH``. When it is
  missing, connecting Claude says to install Git for Windows
  (``winget install Git.Git``) or set ``CLAUDE_CODE_GIT_BASH_PATH``.

*Verified on Windows* (Windows Server 2022; see "Windows validation" in
``agent_execution.rst``). The official CLIs were
installed for a signed-in user the documented ways (Claude Code 2.1.296 by its
native installer and by npm, Codex 0.162.1 by npm, Node.js 24 and Git for
Windows from the machine's installs) after the desktop session started, so the
app's PATH held neither npm's folder nor ``~\.local\bin``. Inside the app,
started through ``Angelo-launcher.exe`` as a desktop launch is,
``windows_agent_checks.py cli`` found both CLIs through the well-known folders
(and Claude's native ``claude.exe`` alone when npm's folder was hidden); npm's
``claude.cmd`` names a native ``claude.exe``, which Angelo starts directly;
``codex.cmd`` became ``node.exe codex.js``. ``claude --version`` and ``claude
auth status`` answered (not signed in) for both installs; Codex's app-server
initialized, ``config/read`` returned Angelo's quoted ``-c`` values intact and
``account/read`` reported no account. Sixteen arguments with JSON, quotes,
``%``, ``!``, ``^``, ``&``, ``|``, ``<``, an empty one, trailing backslashes and
Unicode reached an npm shim's script unchanged, where the same call through
cmd.exe changed ten of them; a plain batch file ran with plain tokens and refused
JSON. A child had no console window (``GetConsoleWindow`` 0; Codex's
``conhost.exe`` showed none) while the app's own console is hidden;
``terminate()`` ended ``codex.exe`` and its ``conhost.exe`` and a sleeping
grandchild; a ``run()`` timeout ended the tree; a normal exit left a grandchild
and a default browser the child opened (Microsoft Edge, through ShellExecute)
running. A cancelled sign-in stand-in (a console helper of its own, then the
default browser) ended its helper and left the fresh browser running; ended
without ``keep_gui_programs``, the same child closed that browser too, which is
why sign-in launches keep windowed programs (a browser already running only
receives the page). Text both ways was UTF-8 (the machine's ANSI code page is 65001, so the
legacy code page this default protects against was not exercised there). The
Git Bash message appeared when Git was hidden from the search; ``claude
--version`` itself runs without Git Bash. Not covered: signing in (``claude auth
login``, ``codex login``) and any turn, Windows 10 and 11 as such, a legacy ANSI
code page, the ChatGPT app's bundled ``codex``, Volta, Scoop, WinGet, pnpm and
nvm-windows installs, and an MSIX-packaged app's environment.

Tested versions (``cli_runtime.RUNTIMES``):

.. list-table::
   :header-rows: 1

   * - Runtime
     - Floor (refused below)
     - Ceiling (gated on; newer runs as usual)
     - What Angelo relies on
   * - Claude Code
     - 2.1.292 (its stable channel; `Claude Code's range`_)
     - 2.1.301 (the release's live gates)
     - ``stream-json`` ``initialize.jsonSchema`` and ``systemPrompt``
   * - Codex
     - 0.160.0
     - 0.162.0 (including 0.162.0-alpha.2, the ChatGPT app's bundled Codex)
     - ``app-server`` ``experimentalApi`` (dynamic tools, code mode) and the
       undocumented ``-c`` keys (feature switches, the developer-message
       switches)

* **Below the floor** nothing is launched past the check: Claude's version probe
  (``claude --version``, every launch) refuses before the process starts;
  Codex's version is read from the ``userAgent`` of its ``initialize`` response
  (the running server, no extra process; its first ``/X.Y.Z`` is the version,
  whatever the client name holds, and a pre-release such as ``-alpha.2`` counts
  as its release) and refused before the first experimental request. A version that cannot be read is refused too, because an
  older runtime may silently ignore the schema or the safety switches Angelo
  sets. The message (``cli_runtime.too_old_message``, the same for both)
  names the version found and how to update: for Claude Code ``claude update``,
  ``npm install -g @anthropic-ai/claude-code`` or ``brew upgrade --cask
  claude-code``.
* **Above the ceiling** the runtime runs exactly as one inside the range.
  Angelo follows the latest releases, and Claude Code and Codex update
  themselves, so most people run a release newer than any ceiling: a newer one
  is never refused, warned about in the chat or asked to downgrade. It is only
  recorded, in one quiet log line on the console per process and version
  (``cli_runtime.note_newer``: ``Angelo: Claude Code 2.1.302 is newer than the
  version Angelo's gates ran on (2.1.301); running it as usual``).
* The ceiling records what the gates ran on, nothing more: bump it in
  ``cli_runtime.RUNTIMES`` after the protocol checks and the live evaluation
  gates have passed on a release, and record the version here. The ceilings
  are the newest versions the documented measurements and live runs used
  (``performance.rst``, ``gpt_launch.rst``).
* Tests: ``test_native_cli_runtime.py``. Test fixtures take their versions from
  ``RUNTIMES`` (``claude_protocol_fixtures.SUPPORTED``, ``TOO_OLD``, ``NEWER``),
  so moving the range changes no fixture.

Claude Code's range
-------------------

Angelo runs on the newest Claude Code, so its range is moved at every Angelo
release, never chosen once (the release step is in ``releasing.rst``):

* **Ceiling:** the newest release the release's live gates ran on.
* **Floor:** the newest release that passed the launch check and that every
  official install channel already delivers, which today is Claude Code's
  ``stable`` channel (the default Homebrew cask, ``claude-code``, follows it).
  A floor above it would refuse people whose Claude Code updates itself on that
  channel, and whom the refusal's ``brew upgrade`` could not help.
* **Launch check:** ``runtime_launch_check.py OUT --claude <path>`` (unbilled:
  a local mock, a dummy key, every other host behind a dead proxy). Launched as
  a chat launches it, the release must answer ``initialize``, send nothing
  before the message, send Angelo's plan schema and instructions in the first
  request and end the turn with the plan, and its first message must be no
  slower than on the floor it replaces.

Releases at or above the floor include Claude Code's fixes for first-turn waits
Angelo's launch can hit (Claude Code's changelog): 2.1.274
stopped ``--strict-mcp-config`` with an empty ``--mcp-config``, Angelo's exact
flags, from holding the first non-interactive turn for up to ``MCP_TIMEOUT`` on
incidental MCP servers, and a ``stream-json`` session's first turn from waiting
up to 2 s for deferred ones; 2.1.277 stopped the first turn of a ``-p`` session
waiting on the per-directory CLAUDE.md lookup. 2.1.300 also gzips large
requests to api.anthropic.com when telemetry is off; relying on that needs a
floor of 2.1.300, which waits for the stable channel.

On the local mock the releases are equally fast: it has none of those waits (no
MCP server, an empty folder), so the floor is chosen for those fixes on real
installs and to follow the releases, not for a gain measured on the mock. A new chat's first
message on an API-key connection, launched at Send (``runtime_launch_check.py
--below-floor --trials 7``, releases alternating, load average 2.8–3.0;
medians, milliseconds; every release sent one request per turn and none before
the message):

.. list-table::
   :header-rows: 1

   * - Claude Code
     - Send to first request
     - Send to turn end
     - Launch on the request's path
   * - 2.1.258 (below the floor)
     - 271
     - 287
     - 171
   * - 2.1.292 (the floor)
     - 271
     - 293
     - 168
   * - 2.1.301 (the ceiling)
     - 279
     - 302
     - 173
   * - 2.1.302 (npm ``latest``)
     - 277
     - 298
     - 172

Saved API keys (OS key stores)
==============================

BYOK keys persist in the operating system's secure store so they survive
restarts and rebuilds without being re-entered. macOS, Windows and Linux have
the same Remember behavior through their own stores, with the same item
identity, worker and deadlines; no platform has a plaintext or file fallback.
It applies to the Claude API key, the
Grok/MiniMax/Kimi coding connections and every media generation key. The OpenAI
coding API mode keeps its key in Codex's own OS keyring, and subscription
sign-ins stay inside the official CLIs; neither is read or copied.

========  =====================================================  ==============================
Platform  Store and backend                                      Form option
========  =====================================================  ==============================
macOS     login Keychain, ``keychain_macos.py``                  Remember in Keychain
Windows   Credential Manager, ``credential_manager_windows.py``  Remember in Credential Manager
Linux     Secret Service keyring, ``secret_service_linux.py``    Remember in your keyring
========  =====================================================  ==============================

* **Owners.** ``key_store.py`` documents the backend contract (read, write with
  read-back, delete, optional ``erase`` and ``probe``), the shared error kinds,
  the per-platform names used in every label, the policy and the selection
  (``open_store``). Each backend is the only caller of its OS API, through
  ctypes: Security (SecItemAdd/Update/CopyMatching/Delete, generic-password
  class), advapi32 (CredWriteW/CredReadW/CredDeleteW/CredFree) and libsecret
  (``libsecret-1.so.0``, loaded only when the store is opened); no Python
  package is needed. ``saved_keys.py`` serializes every call on one worker thread with
  deadlines, holding ``key_store.STORE_CALLS`` (shared with the team sign-in's
  refresh-token and grant-key items; see `Team account and team connections`_);
  ``saved_keys_ui.py`` applies results on the main thread.
  ``ui.py`` keeps owning in-memory keys, checks and controllers. No command
  line (``security``, ``cmdkey`` or ``secret-tool`` would expose the key as an
  argument), file or environment variable is used; the only environment
  injection remains the provider child process
  (``providers.child_environment``), which also drops inherited credential,
  endpoint, model, caching and session variables (``runtime_environment.py``;
  ``model_routing.rst``, "Native child environment").
* **Item identity.** Service ``Angelo API key`` (product-level);
  account ``<connection UUID>@<profile>``, where the profile is the first 16 hex
  digits of SHA-256 of the resolved connections directory. A profile chosen
  with ``ANGELO_CONNECTIONS_DIR`` never reads another profile's keys, even when
  recovered metadata kept the same UUID. Label ``Angelo API key · <service>``.
  On Windows the item is a generic credential with target
  ``Angelo API key:<account>``, user name ``<account>``, the label in its
  comment, local-machine persistence (this user on this computer; it never
  roams) and the key as UTF-8 bytes. On Linux it is an item of schema
  ``org.angelo.ApiKey`` with string attributes ``service`` and ``account``, in
  the collection behind the ``default`` alias (normally the login keyring).
  Moving or migrating the connections folder (as the pre-rename data-folder
  migration does) starts a new, empty namespace: re-enter the keys there, and
  delete the old items in the store's manager if wanted. That migration
  predates saved keys, so it orphans none. The agent state folder's
  migration (``native_editor_build.rst``) does not change the connections
  folder, so it leaves saved keys where they are.
* **Form.** Adding or replacing an API key shows the platform's Remember
  option, on by default. Off keeps the key in memory only and
  deletes any key previously saved for that connection, so an older key cannot
  return after a restart. Editing without a new key applies the choice to the
  current key. Replacing a key updates the item in place; every save is read
  back before it is reported as saved. Removing a connection deletes its item.
  The AI Providers list states where each key lives (saved, memory only,
  saving, or why saving failed; a failed save keeps the key for the session).
* **Startup.** Each API-key connection's item is read off the main thread and
  then applied exactly like a typed key: ``replace_connection_key``
  validation, catalog/native-context invalidation of chats bound to it, and the
  same non-billable connection check. Loading never selects a connection for a
  chat, changes billing or falls back to another account; a key typed while a
  load is pending wins.
* **Prompts and threading.** Startup loads, saves and deletions never show a
  prompt on any platform. The connection menu's **Load Saved Key from …** is
  the only call allowed to show one. Automatic calls have a 10-second deadline
  and the explicit load 120 seconds; on expiry the status fails closed
  (``<Store> didn't respond · re-enter the key``) and a late result is ignored.
  Blender's main thread only enqueues and pumps; ctypes releases the GIL, so an
  open prompt cannot freeze the UI. A deletion still runs after its deadline.

  - *macOS.* The file-based login keychain ignores ``kSecUseAuthenticationUI``
    (it applies only to the data protection keychain), so the process-wide
    ``SecKeychainSetUserInteractionAllowed`` is turned off for each automatic
    call and restored afterwards. A refused explicit load reads
    ``Keychain access denied · re-enter the key``.
  - *Windows.* Generic credentials never prompt, so the explicit load is only
    a retry and there is no approval state.
  - *Linux.* libsecret's convenience calls (``secret_password_store_sync`` and
    ``secret_password_lookup_sync``) unlock a locked keyring by showing its
    unlock prompt and cannot be told not to, so they are not used. Automatic
    reads search without ``SECRET_SEARCH_UNLOCK``; a locked keyring is reported
    as ``Keyring locked · choose Load Saved Key to unlock it, or re-enter the
    key``, and saves and deletions refuse a locked keyring instead of
    unlocking it. Load Saved Key searches with ``SECRET_SEARCH_UNLOCK``, which
    may show the desktop's unlock prompt; a dismissed prompt reads
    ``Keyring access denied · re-enter the key``. A provider that asks on its
    own (for example to confirm access) is bounded by the same deadlines.
* **Availability.** ``open_store`` runs on the main thread and only loads the
  OS library and checks local state; the store's own check (``probe``) runs
  first on the worker. When the store can't be used at all, keys stay in
  memory for the session and the form and list say why, exactly as on a
  platform without a store. Linux needs a session bus address
  (``DBUS_SESSION_BUS_ADDRESS`` or ``$XDG_RUNTIME_DIR/bus``; an X11
  ``dbus-launch`` is never started), ``libsecret-1.so.0`` with every function
  used, a running Secret Service and a ``default`` collection; SSH sessions,
  containers, headless machines and desktops without a Secret Service
  provider therefore stay memory-only. In a KDE Plasma session the reason
  names KDE Wallet's setting; inside a Flatpak without access to the keyring
  service it gives the ``flatpak override`` command (see `Linux keyrings,
  KDE, Flatpak and Snap`_). Windows needs a credential vault in the
  sign-in session (``ERROR_NO_SUCH_LOGON_SESSION`` otherwise, for example in an
  SSH session signed in with a key). A locked keyring is not unavailable: it
  is the approval state above. Other platforms are memory-only.
* **Another build's item** (macOS; measured on macOS 26.3 with prompts
  disallowed; this is what an ad-hoc development app sees after a rebuild, or
  any app whose code identity changed). Reading fails at once with
  errSecAuthFailed and deleting with errSecInvalidOwnerEdit; a locked keychain
  fails with errSecInteractionNotAllowed. All mean *approval needed*, shown as
  ``Keychain approval needed · choose Load Saved Key, or re-enter the key``.
  Overwriting the secret succeeds, but afterwards no build can read it without
  approval, so such a save is labeled ``Key saved in Keychain · this build
  needs approval to read it (Load Saved Key)`` and Load Saved Key is offered.
  When a removal cannot delete the item, the secret is overwritten with
  nothing (evidenced by its OSStatus; reading it back would need a prompt), an
  empty item reads as absent, and AI Providers names the empty item to delete
  in Keychain Access. Windows and Linux items have no per-build owner, so this
  does not arise there.
* **Development builds** (macOS). The development app is ad-hoc (linker)
  signed, so its designated requirement and Keychain partition are its
  per-build cdhash: after every rebuild, saved keys need approval until Load
  Saved Key is approved (macOS asks for the login password; **Always Allow**
  lasts until the next rebuild). To avoid this, set ``ANGELO_DEV_SIGN_ID`` to
  an **Apple Development** identity (free with an Apple Account: Xcode >
  Settings > Accounts > Manage Certificates); ``build_native.sh`` then
  re-signs the executable through ``dev_codesign.sh``, and one Always Allow
  should last across rebuilds. Apple's ``securityd``
  (``ClientIdentification::partitionIdForProcess``) gives Apple-issued
  development and Developer ID signatures a ``teamid:`` partition; any other
  signature, including a self-signed certificate, gets ``cdhash:``, so a
  self-signed identity does not stop the per-rebuild approval. Items are never
  created with an any-application access list. The approval prompt and the
  developer-signed path were not exercised by an automated run.
* **Unattended runs.** ``ANGELO_DISABLE_KEYCHAIN=1`` (or a deprecated
  spelling; the ``ANGELO_`` name wins, through ``product_env``) turns every
  backend off for a session (memory-only, stated in the form); the name
  predates the Windows and Linux stores and is kept because suites, the
  evaluation harness and shells already set it. Background mode (``-b``) is
  always memory-only. ``run_native_suites.py`` sets the switch for every suite
  (LIVE included) and the live evaluation harness for every trial, besides
  isolating the connections profile. Even without the switch, automatic calls
  cannot hang on a prompt.
* **Who can read a saved key.** macOS limits an item to the app build that
  created it (or one the person approved). Windows Credential Manager and an
  unlocked Secret Service collection have no per-application access list: any
  program running as the same user (Windows) or in the same desktop session
  (Linux) can read the item. Both encrypt it at rest for that user. The Linux
  details per provider and sandbox are in the matrix below.
* **Size.** Credential Manager stores at most 2,560 bytes per item; a longer
  key is labeled ``Not saved in Credential Manager (key too long) · key kept in
  memory for this session``. Keychain and Secret Service items have no
  practical limit for an API key.
* **Limits.** Another running instance's newly saved or replaced key is picked
  up at the next launch or with Load Saved Key. Removing a connection while
  the store is turned off cannot delete its item. Keys copied into Python
  strings cannot be zeroized; they stay in process memory like any other
  Python string.
  The ctypes buffers that pass a key to Credential Manager or libsecret, and
  the blob Credential Manager returns, are zeroed after use.

Linux keyrings, KDE, Flatpak and Snap
-------------------------------------

Based on the sources listed in `Saved-key sources`_. No
row has run on a real Linux system yet; see `How to continue (saved keys)`_.

.. list-table:: Saved API keys on Linux
   :header-rows: 1
   :widths: 24 22 54

   * - Environment
     - Result
     - Who can read a saved key, and notes
   * - GNOME Keyring (GNOME and other desktops running
       ``gnome-keyring-daemon``)
     - Saved in the login keyring
     - Any program in the desktop session while the login keyring is
       unlocked; encrypted at rest with the login password. Automatic reads
       never prompt; a locked keyring is the approval state.
   * - KWallet, KDE Frameworks 5.97 or later (checked: Debian 12 5.103,
       Kubuntu 24.04 5.115, Leap 15.6 5.115, EPEL 9 5.116, Debian 13 6.13,
       Leap 16 6.16, Fedora 43/44 6.30)
     - Saved in the default wallet through KWallet's Secret Service, which is
       on by default
     - Any program in the session while the wallet is open. KWallet stores
       item attributes (here the service name and ``<uuid>@<profile>``
       account, never the key) unencrypted in ``_attributes.json``; the key is
       encrypted with the wallet. Searching a closed wallet does not open it,
       so automatic reads never prompt. Requires KWallet on, its Secret
       Service interface on, and kwalletd (ksecretd from 6.14) running:
       KWallet installs no D-Bus activation file for
       ``org.freedesktop.secrets``. Frameworks 6.14 to 6.29 can report a wallet
       unlocked at login as locked (fixed in 6.30), which reads as the
       approval state; Load Saved Key should then complete the read
       (unverified).
   * - KWallet, KDE Frameworks 5.96 or earlier (checked: Kubuntu 22.04 5.92,
       Debian 11 5.78, Leap 15.4 5.90, EPEL 8 5.96)
     - Memory-only with the KDE reason, unless another provider runs
     - KWallet has no Secret Service there. Installing GNOME Keyring, or
       enabling KeePassXC's integration, makes that provider's row apply.
   * - KDE with KWallet or its Secret Service interface off, kwalletd not
       running, or no wallet yet
     - Memory-only with a KDE reason
     - ``KDE Wallet isn't providing the keyring service: turn on KDE Wallet
       and “Use KWallet for the Secret Service interface” in System Settings,
       then restart Angelo``, or ``KDE Wallet has no default wallet yet;
       create one in KDE Wallet Manager, then restart Angelo``.
   * - KeePassXC with Secret Service integration (off by default)
     - Saved in the database group exposed to Secret Service
     - Any program in the session while the database is unlocked, subject to
       KeePassXC's own confirmation. Its defaults **Confirm when passwords are
       retrieved by clients** and **Prompt to unlock database before
       searching** make KeePassXC show dialogs even for startup loads; Angelo
       cannot suppress a provider's own dialog and abandons the call after 10
       seconds (``Keyring didn't respond``). Turn both off for prompt-free
       startup, or answer the dialogs.
   * - Flatpak with ``--talk-name=org.freedesktop.secrets`` (manifest or
       ``flatpak override``)
     - As the host provider's row
     - The same keyring and rules as an unsandboxed Angelo. The app can then
       read any item in the unlocked keyring, as any host program can.
   * - Flatpak without that permission
     - Memory-only; the reason gives the ``flatpak override`` command
     - Decided from ``/.flatpak-info`` before libsecret is loaded.
   * - Snap, classic confinement (``release/freedesktop/snap`` template)
     - As the host provider's row
     - Classic snaps are not D-Bus confined.
   * - Snap, strict confinement (not used)
     - Would need the ``password-manager-service`` plug
     - See the Snap entry below.
   * - Headless, SSH without a forwarded session bus, containers, ``-b``
     - Memory-only
     - No session bus address (an X11 ``dbus-launch`` is never started), or
       background mode.

Design choices:

* **KDE uses the existing Secret Service backend; there is no native KWallet
  backend.** KWallet has served ``org.freedesktop.secrets`` since KDE
  Frameworks 5.97 (August 2022), on by default (``apiEnabled`` defaults to
  true in kwalletd and, from 6.14, in ksecretd). The newer releases of every
  distribution we checked ship 5.97 or later; Kubuntu 22.04, Debian 11, Leap
  15.4 and EPEL 8 ship older Frameworks, where GNOME Keyring or KeePassXC can
  be the provider instead. A KWallet D-Bus backend could not keep this
  section's contract: KWallet's own API documents that opening a wallet
  prompts ("The user will be prompted to allow your application to open the
  wallet, and may be prompted for a password"), and it would be a second,
  untested Linux backend.
* **KDE gets an actionable reason instead of the generic one.** A KDE session
  is ``XDG_CURRENT_DESKTOP`` containing ``KDE`` or ``KDE_FULL_SESSION=true``
  (both set by ``startplasma``). When no Secret Service answers there, the
  reason names the System Settings switches (Plasma 6: Security & Privacy >
  KDE Wallet; Plasma 5: Personalization > KDE Wallet; KDE Gear 22.08 and 22.12
  label the switch "Enable Secret Service DBus API"; without the settings
  module, ``[org.freedesktop.secrets] apiEnabled=true`` and ``[Wallet]
  Enabled=true`` in ``~/.config/kwalletrc``). Angelo does not start kwalletd
  itself: activating it would claim ``org.freedesktop.secrets`` for KWallet
  even when the person uses another provider (for example KeePassXC) that is
  not running yet.
* **Flatpak uses the existing path, which needs
  ``--talk-name=org.freedesktop.secrets``; the Secret portal is not used.**
  libsecret keeps keys in a per-app file through the Secret portal only for
  its simple ``secret_password_*`` calls, which unlock a locked keyring with a
  prompt when they fall back to the Secret Service; that is why this backend
  uses the SecretService/Collection/Item calls. The portal itself cannot keep
  the no-prompt contract either: its first use in a process asks the desktop's
  portal backend for the app's master secret, and GNOME's ``oo7-portal`` (first
  in GNOME's portal preference) unlocks a locked collection with a prompt, while
  KWallet's portal (Frameworks 6.2 and later) opens the wallet first. Of the
  three backends checked, only GNOME Keyring's answers without UI. Keeping
  the contract would mean contacting the portal only from Load Saved Key, once
  per launch, with saves failing until then. The finish-arg instead gives the
  same behavior and prompts as outside the sandbox; its cost is that Angelo
  can read any item in the unlocked keyring, which an unsandboxed Angelo can
  already do.
* **Sandbox detection reads ``/.flatpak-info``.** It is the file libsecret
  uses to detect Flatpak and holds the sandbox's effective permissions,
  overrides included. ``FLATPAK_ID`` alone is not trusted, since a host process
  can inherit it. A hidden bus name answers like a missing service, so the
  file's ``[Session Bus Policy]`` (``talk`` or ``own`` on the name or a prefix
  such as ``org.freedesktop.*``) or a ``session-bus`` socket is the only way
  to tell a sandbox refusal from a desktop without a provider. An unreadable
  file falls back to the D-Bus probe.
* **Snap: documentation only.** Angelo's snap template uses classic
  confinement, which has no D-Bus confinement, so the host behavior applies.
  A strictly confined snap would need the ``password-manager-service`` plug.
  snapd does not auto-connect it (``deny-auto-connection: true``); the person
  runs ``snap connect <snap>:password-manager-service``. It grants the whole
  Secret Service and KWallet D-Bus APIs; snapd's own description says this
  "does not allow for application isolation". libsecret's ``SNAP_NAME`` rule
  only changes its simple calls, which Angelo does not use.

Flatpak packaging. Angelo ships no Flatpak manifest. A manifest needs, for
saved keys, only::

    finish-args:
      - --talk-name=org.freedesktop.secrets

The Freedesktop runtime already contains libsecret (0.21.8.2 with libgcrypt
on freedesktop-sdk master), and Flatpak provides a filtered session bus by
default. Without the finish-arg, keys stay in memory and the form shows
``This Flatpak has no access to your keyring service; to allow it, run:
flatpak override --user --talk-name=org.freedesktop.secrets <app ID>``.

Manual checks on macOS
----------------------

``test_blender_keychain_live.py`` covers the Security framework with prompts
disallowed. By hand, with a throwaway test key and an isolated profile
(``ANGELO_CONNECTIONS_DIR`` pointing at an empty folder, and
``ANGELO_DISABLE_KEYCHAIN`` unset):

1. Add a Grok API connection with the test key. The form shows **Remember in
   Keychain**, on; the list says ``Key saved in Keychain``. Keychain Access
   (login keychain) shows ``Angelo API key · Grok API``, with service
   ``Angelo API key`` and account ``<uuid>@<profile>``.
2. Restart Angelo: the key loads without a prompt.
3. Rebuild (ad-hoc signed) and restart: the connection reads ``Keychain
   approval needed · choose Load Saved Key, or re-enter the key`` with no
   prompt at startup. **Load Saved Key from Keychain** asks for the login
   password; **Always Allow** loads it until the next rebuild.
4. Remember off removes the item, and so does removing the connection. On a
   rebuilt app that was not approved, removal erases the secret and the list
   names the empty item to delete in Keychain Access.
5. ``ANGELO_DISABLE_KEYCHAIN=1`` starts memory-only and says so.

Manual checks on Windows
------------------------

Not yet run on Windows: the host tests drive the real binding through a fake
advapi32, which cannot prove Windows' own behavior. Use a throwaway test key
(never a real one) in an isolated profile (``ANGELO_CONNECTIONS_DIR`` pointing
at an empty folder), and leave ``ANGELO_DISABLE_KEYCHAIN`` unset.

1. Add a Grok API connection with the test key. The form shows **Remember in
   Credential Manager**, on. The list then says ``Key saved in Credential
   Manager``.
2. In Control Panel > Credential Manager > Windows Credentials, a generic
   credential ``Angelo API key:<uuid>@<profile>`` exists, with the label in
   its comment. ``cmdkey /list:"Angelo API key*"`` lists it without showing
   the secret.
3. Restart Angelo: the key loads without a prompt and the connection check
   runs. No key appears in ``connections.json`` or any log.
4. Edit the connection with Remember off: the credential disappears and the
   list says ``Key in memory only``. Turn it back on without retyping: it
   returns.
5. Replace the key with a different test key, restart: the new key loads.
6. Remove the connection: the credential disappears.
7. Start Angelo with ``ANGELO_DISABLE_KEYCHAIN=1``: the form states that
   Credential Manager is turned off, and nothing is written.
8. Over SSH signed in with a key (no credential vault), the form states that
   Credential Manager isn't available in this sign-in session.
9. A key longer than 2,560 bytes is kept in memory and labeled ``key too
   long``.

Manual checks on Linux
----------------------

Not yet run on Linux: the host tests drive the real binding through a fake
libsecret, which cannot prove a real desktop's behavior. Use a throwaway test
key and an isolated profile as above, on a desktop with a Secret Service
provider (GNOME Keyring, or KWallet with its Secret Service interface
enabled) and ``libsecret-1-0`` installed.

1. Add a Grok API connection with the test key. The form shows **Remember in
   your keyring**, on, and the list says ``Key saved in your keyring``.
2. ``secret-tool search service 'Angelo API key'`` (or the keyring manager)
   shows one item with ``account=<uuid>@<profile>`` and
   ``xdg:schema=org.angelo.ApiKey``, labeled ``Angelo API key · Grok API``.
   Note that ``secret-tool search`` prints the secret: use only the test key.
3. Restart Angelo with the keyring unlocked: the key loads without a prompt.
4. Lock the keyring (in the keyring manager), restart Angelo: no unlock prompt
   appears at startup; the connection reads ``Keyring locked · choose Load
   Saved Key to unlock it, or re-enter the key``. Choose **Load Saved Key from
   your keyring**: the unlock prompt appears; unlocking loads the key, and
   canceling reads ``Keyring access denied · re-enter the key``.
5. Leave that prompt unanswered for two minutes: the status becomes
   ``Keyring didn't respond · re-enter the key`` and Blender stays responsive.
6. Remember off, Remember on, replacement and removal behave as on Windows
   (steps 4 to 6), checked with ``secret-tool search``.
7. ``ANGELO_DISABLE_KEYCHAIN=1``, an SSH session without a session bus, a
   machine without ``libsecret-1.so.0`` and a session with no Secret Service
   (for example a window manager without GNOME Keyring) each start memory-only
   and state why; nothing is written anywhere.
8. KDE Plasma (Frameworks 5.97 or later): repeat steps 1 to 6 with KWallet.
   Close the wallet (KDE Wallet Manager > Close) for step 4: no wallet dialog
   appears at startup, and Load Saved Key shows KWallet's password dialog.
   ``busctl --user status org.freedesktop.secrets`` names the owner.
9. KDE with **Use KWallet for the Secret Service interface** off (or
   Frameworks 5.96 or older, for example Kubuntu 22.04): Angelo starts
   memory-only with the KDE reason. Turn it on, restart Angelo: keys save.
   Also note whether ``org.freedesktop.secrets`` is owned right after login,
   before any KWallet client has run.
10. KeePassXC with Secret Service integration on and GNOME Keyring absent:
    with the defaults, record which KeePassXC dialogs appear at startup and
    that Angelo stays responsive; with both confirmations off, none appear.
11. Flatpak: in a test Flatpak of Angelo without the finish-arg, the form
    shows the ``flatpak override`` reason; after ``flatpak override --user
    --talk-name=org.freedesktop.secrets <app ID>`` and a restart, steps 1 to
    6 pass and the item appears in the host's keyring manager.

How to continue (saved keys)
----------------------------

Untested on real systems: the Windows backend; the Linux backend on any
desktop (GNOME Keyring, KWallet on Frameworks 5 and 6, KeePassXC); KDE's
locked-flag behavior on Frameworks 6.14 to 6.29; whether kwalletd or ksecretd
already owns ``org.freedesktop.secrets`` right after a Plasma login; the
``/.flatpak-info`` parsing on a real sandbox; the macOS approval prompt and
developer-signed path. Host tests use fakes at the ctypes boundary and the
real ``configparser``; they prove marshalling and policy, not the desktops.
The Linux code is ``secret_service_linux.py`` (binding, ``SecretServiceStore``,
``kde_session``, ``flatpak_bus_access`` and the reasons) and its tests are
``test_native_secret_service.py`` and ``test_native_key_store.py``; run them
with ``python3 tests/python/angelo_agent/run_host_tests.py``.

Real-environment tests, in order of value per cost (sizes are approximate;
check the mirror first):

1. **Headless libsecret and GNOME Keyring, automatable in CI.** An
   ``ubuntu:24.04`` container (about 30 MB compressed) or the GitHub
   ``ubuntu-latest`` runner, plus the ``dbus``, ``gnome-keyring``,
   ``libsecret-1-0`` and ``libsecret-tools`` packages (on the order of 100 MB).
   Under ``dbus-run-session``, start ``gnome-keyring-daemon --unlock`` with a
   throwaway password and drive ``SecretServiceStore`` from host Python, with
   no Blender build: round trip, replace, delete, locking the collection over
   D-Bus and checking that automatic reads report ``approval``. Without a
   display no prompter can start, so an attempted prompt fails or hangs until
   the 10-second deadline; either is detected. This is the cheapest proof that
   the ctypes binding matches the real library.
2. **KDE virtual machines, manual checks 8 and 9.** Fedora KDE Plasma Desktop
   44 live image (Frameworks 6.30, about 3 GB) and Kubuntu 24.04 (Frameworks
   5.115, about 4 GB) for the KWallet rows; Kubuntu 22.04 (Frameworks 5.92,
   about 4 GB) for the KDE reason with no Secret Service. A release with
   Frameworks 6.14 to 6.29 (Leap 16.0 ships 6.16, Ubuntu 25.10 ships 6.17) for
   the locked-flag behavior.
3. **Flatpak, manual check 11, in one of those virtual machines.** A minimal
   test Flatpak on ``org.freedesktop.Platform`` (24.08 or later; several
   hundred MB) running the same Python driver as test 1, with and without the
   finish-arg, plus an override. This checks the ``/.flatpak-info`` reading
   and that the host keyring is reachable through the proxy.
4. **Windows**, the manual checks above, on any Windows 10 or 11 machine or
   evaluation virtual machine (about 20 GB).

Saved-key sources
-----------------

* libsecret: backend choice in
  `secret-backend.c <https://gitlab.gnome.org/GNOME/libsecret/-/blob/main/libsecret/secret-backend.c>`__
  (file backend only with ``/.flatpak-info`` or ``SNAP_NAME`` and Secret
  portal version 1);
  `secret-file-backend.c <https://gitlab.gnome.org/GNOME/libsecret/-/blob/main/libsecret/secret-file-backend.c>`__
  (portal check, ``keyrings/default.keyring`` under ``XDG_DATA_HOME``,
  ``RetrieveSecret`` errors);
  `secret-password.c <https://gitlab.gnome.org/GNOME/libsecret/-/blob/main/libsecret/secret-password.c>`__
  (simple calls through ``secret_backend_get``);
  `NEWS <https://gitlab.gnome.org/GNOME/libsecret/-/blob/main/NEWS>`__
  (file backend 0.20.0, snap detection 0.20.5); D. Ueno,
  `Secrets management for Flatpak applications <https://opensource.com/article/19/11/secrets-management-flatpak-applications>`__,
  2019-11-11 ("Local storage is only supported in the simple API").
* Secret portal:
  `interface <https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.Secret.html>`__;
  `frontend <https://github.com/flatpak/xdg-desktop-portal/blob/main/desktop-portal/secret.c>`__
  (forwards the app ID, no dialog of its own); GNOME Keyring backend
  `gkd-secret-portal.c <https://gitlab.gnome.org/GNOME/gnome-keyring/-/blob/main/daemon/dbus/gkd-secret-portal.c>`__
  (no prompt; fails while locked); oo7
  `portal <https://github.com/bilelmoussaoui/oo7/blob/main/portal/src/main.rs>`__
  and `Service.unlock <https://github.com/bilelmoussaoui/oo7/blob/main/client/src/dbus/api/service.rs>`__
  (unlocks with a prompt);
  `gnome-portals.conf <https://gitlab.gnome.org/GNOME/gnome-session/-/blob/main/data/gnome-portals.conf>`__
  (``oo7-portal;gnome-keyring;``); KWallet
  `kwalletportalsecrets.cpp <https://invent.kde.org/frameworks/kwallet/-/blob/master/src/runtime/ksecretd/kwalletportalsecrets.cpp>`__
  (opens the network wallet).
* KWallet:
  `Frameworks 5.97 <https://kde.org/announcements/frameworks/5/5.97.0/>`__
  ("Introduce Secret Service API");
  `kwalletd.cpp at v5.97.0 <https://invent.kde.org/frameworks/kwallet/-/blob/v5.97.0/src/runtime/kwalletd/kwalletd.cpp>`__
  (``apiEnabled`` defaults to true);
  `MR 33 <https://invent.kde.org/frameworks/kwallet/-/merge_requests/33>`__
  (disabled API frees the name, 5.98);
  `MR 97 <https://invent.kde.org/frameworks/kwallet/-/merge_requests/97>`__
  and `Frameworks 6.14 <https://kde.org/announcements/frameworks/6/6.14.0/>`__
  (ksecretd); `Frameworks 6.2 <https://kde.org/announcements/frameworks/6/6.2.0/>`__
  (Secret portal); `Frameworks 6.30 <https://kde.org/announcements/frameworks/6/6.30.0/>`__
  (locked state after PAM unlock);
  `kwalletfreedesktopcollection.cpp <https://invent.kde.org/frameworks/kwallet/-/blob/master/src/runtime/ksecretd/kwalletfreedesktopcollection.cpp>`__
  (``default`` alias, lock state, search without opening);
  `kwallet.h <https://invent.kde.org/frameworks/kwallet/-/blob/master/src/api/KWallet/kwallet.h>`__
  (``openWallet`` prompts);
  `walletconfigwidget.ui <https://invent.kde.org/utilities/kwalletmanager/-/blob/v26.08.0/src/konfigurator/walletconfigwidget.ui>`__
  (setting label);
  `startplasma.cpp <https://invent.kde.org/plasma/plasma-workspace/-/blob/master/startkde/startplasma.cpp>`__
  (``XDG_CURRENT_DESKTOP=KDE``, ``KDE_FULL_SESSION=true``). Distribution
  versions from packages.ubuntu.com, packages.debian.org, packagehub.suse.com
  and packages.fedoraproject.org.
* KeePassXC:
  `Config.cpp <https://github.com/keepassxreboot/keepassxc/blob/develop/src/core/Config.cpp>`__
  (integration off; access confirmation and unlock-before-search on) and
  `SettingsWidgetFdoSecrets.ui <https://github.com/keepassxreboot/keepassxc/blob/develop/src/fdosecrets/widgets/SettingsWidgetFdoSecrets.ui>`__
  (labels).
* Flatpak: `flatpak-metadata(5) <https://docs.flatpak.org/en/latest/flatpak-command-reference.html#flatpak-metadata>`__
  (``/.flatpak-info`` is the effective configuration; ``[Session Bus
  Policy]`` values none, see, talk, own); ``flatpak run`` sets ``FLATPAK_ID``;
  `xdg-dbus-proxy <https://github.com/flatpak/xdg-dbus-proxy/blob/main/flatpak-proxy.c>`__
  (hidden names answer ``ServiceUnknown`` or ``NameHasNoOwner``);
  freedesktop-sdk
  `libsecret.bst <https://gitlab.com/freedesktop-sdk/freedesktop-sdk/-/blob/master/elements/components/libsecret.bst>`__
  and ``platform.bst``.
* Snap: snapd
  `password_manager_service.go <https://github.com/canonical/snapd/blob/master/interfaces/builtin/password_manager_service.go>`__.

Team account and team connections
=================================

A person can sign in to Angelo with their Angelo organization account (live
checks: `Live verification`_); while signed in, AI Providers lists the
organization's **team connections** under **Angelo team**, and the status bar
shows the account (`Account in the status bar`_). Personal connections keep
working for everyone, with or without a team, unless the
organization's managed policy turns them off (``doc/angelo/enterprise_policy.rst``).

* **What routes where.** A personal connection talks to its provider
  directly with the person's own key or native sign-in; it is never
  intermediated, and subscription credentials are never extracted or
  proxied. A team connection deliberately routes the organization's own
  provider keys through the organization's Angelo gateway: the app sends
  ``{gateway}/v1/<provider>/<the provider's own path>`` with the person's
  short-lived Angelo credential, and the gateway applies the key an admin
  added. The person never sees or holds a provider key.
* **Origins.** ``ANGELO_CONSOLE_URL`` (default
  ``https://console.angelo.studio``) and ``ANGELO_GATEWAY_URL`` (default
  ``https://gateway.angelo.studio``), read by ``team_endpoints.py``. A managed
  policy's ``ConsoleURL`` / ``GatewayURL`` wins over them. An origin is
  ``https://<host>`` on the default port, or plain ``http://`` only for an
  explicitly configured loopback host (development). Anything else disables
  the team account with a message naming the variable or policy key.
* **Trust.** Console and gateway requests verify TLS with Python's default
  roots, which inside Angelo are the bundled certifi file Blender names in
  ``SSL_CERT_FILE``, plus the policy's ``ExtraCACertificates`` (an
  organization's TLS-inspection root). Blender's own Python has no other
  roots on macOS (``ssl.get_default_verify_paths()`` is ``/dev/null``), so a
  script outside the app uses certifi directly (``team_http.tls_context``).
  Whatever root vouched for it, a chain holding a distrusted key
  (``tls_distrust``, ``releasing.rst``, "Bundled trust stores") fails as a
  certificate verification error.
  HTTP proxies are not used yet (see `Not yet`_).

Sign-in
-------

OAuth authorization code with PKCE S256 for a native app (RFC 8252), client
``angelo-desktop``, scope ``api generate offline_access``
(``team_signin.py``). **Sign in to Angelo team** opens the system browser at
``{console}/oauth/authorize`` with a random ``state`` and the computer's short
host name as ``device_name``; the console signs the person in (its own sign-in,
email code, Google, GitHub or the organization's SSO: the browser handles all
of it, the app only waits) and shows its allow screen, where the person picks
the organization (one per sign-in). The answer arrives at
``http://127.0.0.1:<random port>/callback`` with ``code`` (or ``error``),
``state`` and ``iss`` (RFC 9207); a callback whose ``iss`` is not the console
the sign-in started at is refused (mix-up), as is a wrong ``state``.

That callback listener is Angelo's **only network listener**, a deliberate and
narrow exception: it binds 127.0.0.1 (never ``localhost`` or another
interface) on a port the OS chooses, exists only while a sign-in waits for the
browser, and closes once the browser has seen the outcome, on Cancel, or after
10 minutes. Requests are never logged (the query carries the code). The
authorization link can be copied when the browser doesn't open; it holds no
secret. With a managed ``Organization`` the link carries ``org_hint``, which
pre-selects it on the allow screen; after the console ended a sign-in (not
after the person's own Sign out, nor for an account the policy refused) it
carries ``login_hint``, that account's email, which fills in the console's
email step. A console without the hints, or one that finds a hint malformed,
ignores it.

**The browser's pages** (``team_pages.py``, in the console's design).
``/callback`` answers ``303 See Other`` to
``/result`` (or ``/mismatch``), so the code never stays in the address bar or
history. ``/result`` waits up to 25 seconds for the token exchange and says
what really happened: *You're signed in* (Angelo on <device> can now use
<team>, a link to Your devices), *signed in without a seat* (with the admin
contact), *This team isn't allowed here* (the managed policy's reason),
*Sign-in cancelled*, *This sign-in expired*, *Angelo couldn't finish signing
in* (unreachable or refused), or *Finishing your sign-in*. A wrong ``state``
or ``iss`` shows *This sign-in doesn't match* and the sign-in keeps waiting; a
repeated callback shows *already complete*; any other path is 404. Each page
is the console's FullPage design (the wordmark from the brand asset, inline;
one panel; the gallery wall in dark, the sketchbook paper in light;
responsive; reduced motion respected), self-contained under a strict
Content-Security-Policy (``default-src 'none'``, the inline style allowed by
its hash, no script), with fields escaped and its text translated on the main
thread before the listener starts (``lang`` and ``dir`` follow the interface
language). ``test_native_team_pages`` covers every state; the pages were
checked in light and dark at desktop and phone widths.

Tokens, single flight and grant keys
------------------------------------

``team_account.py`` (bpy-free) owns the credentials:

* the **access token** (10 minutes) is in memory, and the latest one is shared
  by every Angelo process on the computer through a third store item (service
  ``studio.hyperbond.angelo.team-access-token``, ``team_access_share.py``; see
  *Refresh cadence* below);
* the **refresh token** is kept in the OS credential store through the same
  backends as saved API keys (``team_token_store.py``): service
  ``studio.hyperbond.angelo.team-sign-in`` (named from the bundle identifier;
  Windows target ``<service>:<account>``), account ``<console id>@<profile>``
  (a development console never reads the production sign-in; profiles are
  isolated as for API keys), label ``Angelo team sign-in · <console host>``.
  Never a file, setting or log. When the store can't be used
  (``ANGELO_DISABLE_KEYCHAIN=1``, background mode, no keyring) the sign-in
  lasts until Angelo quits, and AI Providers says so;
* the coding agents' **grant key** (``team_grants.py``) is in memory and, so
  that every window and restart reuses one key, in a second store item
  (service ``studio.hyperbond.angelo.team-grant-key``, value
  ``v1 <expiry> <org> <user> <key>``; another person's or organization's key
  is never adopted).

On macOS a Keychain item belongs to the app that created it: another build of
Angelo (for example a rebuilt development app) reads it as "needs approval",
which never prompts here and asks the person to sign in again.

**Single flight.** Refresh tokens rotate and a presented-twice token ends the
whole grant on the console (reuse detection), and every Angelo window and
instance on a computer shares one grant. So a refresh holds ``_refresh_lock``
in the process and ``team_lock`` across processes: an OS file lock on
``team-account.lock`` in the connections folder (``fcntl.flock`` on macOS and
Linux, ``msvcrt.locking`` on Windows, both taken without blocking and retried
for up to 60 seconds, because ``msvcrt.LK_LOCK`` gives up after about 10; the
OS releases it when a process dies). Under the lock the stored token is read
again: a stored token different from this process's and from the one it last
saw means another window rotated it, and that one is presented. A lock
timeout never presents the token (*Another Angelo window is renewing your
team sign-in*). At exit, ``TeamAccount.drain`` waits up to 10 seconds for a
refresh in flight to store its rotated token, because the worker threads are
daemons: a quit between the console's answer and the store write would leave a
used token for the next launch. ``test_native_team_account`` races four real
processes restoring one stored sign-in (all rotate in turn, nothing revoked)
and a control without the lock (reuse detection trips).

**Refresh cadence.** A new process holds no access token, so without sharing
every start would rotate the sign-in (a test profile whose trials each start a
fresh Angelo process would refresh every few seconds, although a token lasts
600). A process refreshes only when it holds no
access token with more than 60 seconds left or the server refused it; reads
(providers, usage, Your devices, the library) reuse it, and a restore's queued
refresh is skipped when a caller already refreshed. And the latest token
answer is kept in the OS store next to the refresh token: under the team lock
a starting or renewing process adopts it while it has more than 60 seconds
left, so N processes within one lifetime cause at most one refresh
(``test_native_team_refresh_cadence``, also with real processes).

*Security.* The access token item has exactly the refresh token item's
protection (same store, same per-app ownership on macOS), and whoever can read
one can read the other; the refresh token is the stronger credential (60 days,
and it mints access tokens), so the stored access token adds no new exposure:
at most 10 minutes of the access the refresh token already grants, erased with
the sign-in (Sign out, an ended sign-in, a policy refusal). A stored token is
used only with the refresh token it came with (a SHA-256 prefix of it, so
never one of an older rotation or another sign-in), never past 15 minutes
whatever its stored expiry, never when it is the token the server just refused
or the one the process already holds, and its account fields pass the same
validation and managed-policy admission as a console answer. Fewer rotations
also mean fewer chances of the dangerous case, an answer lost after the
console rotated (the sign-in ends), and of reuse detection under concurrency.
A value over 2,048 bytes (Windows Credential Manager holds 2,560) is not
shared, and the older one is deleted.

A token is presented at most once. If the request certainly never reached the
console (DNS, refused connection, TLS handshake) or the console refused it
unprocessed (429, 503), the token is kept and Angelo retries every minute
(*offline*). If the answer was lost after sending, the sign-in ends rather
than risk presenting a used token. ``invalid_grant`` ends it with the
console's reason, from ``error_reason``: ``member_removed`` (left,
removed, or removed by the directory) *You're no longer a member of this
Angelo team*; ``expired`` (a refresh token unused for 60 days) *Your Angelo
sign-in expired after a long time unused*; ``reuse_detected`` (a rotated
token came back) and ``code_reused`` (an authorization code was replayed),
each of which ended the sign-in, *signed out … for safety: its sign-in was
used twice*; ``signed_out`` (Your devices, an admin, an unknown token) and any
reason this build doesn't know, *This computer was signed out of your Angelo
team*. A console without ``error_reason`` is read from its descriptions: a
removed member by name, everything else as signed out. At the code exchange,
``member_removed`` says so on the result page; every other refusal shows
*This sign-in expired*. If saving a rotated token
fails, the stored one is deleted so a used token is never presented at the
next launch.

**Grant keys** (``POST /api/oauth/grant-key``, 12 hours; the console keeps at
most 8 unexpired per grant and revokes the oldest beyond that). A key is
fetched when the account holds a seat and the organization offers a coding
provider: first from the shared item (when it is this person's and has more
than two hours left), else issued and shared, under the team lock. It is
renewed when less than two hours remain and no chat is running a turn, or
when less than 20 minutes remain in any case, so a computer issues about one
key every 10 hours whatever the number of windows and restarts. A runtime's
key is fixed for its lifetime, so a chat picks up a renewed key at its next
request: ``Controller.select`` sees a different key, closes the warm runtime
and the next one resumes the same native session (a team key change is not a
new credential scope; only ``*_api`` modes drop contexts on a key change;
``RenewalTests``). A turn running when its key expires fails; the next request
uses the new key. When a team chat fails on sign-in with the current key (the
gateway refused it: the 8-key cap, a removed seat), the key is dropped once
and another is issued.

**Sign out** forgets the tokens and team connections at once, then revokes the
refresh token on the console (which ends the grant and its keys) and erases
both store items in the background. It is refused while a chat is running a
turn on a team connection. Closing Angelo keeps the sign-in for the next
launch.

Team connections
----------------

Team connections are **virtual** (``team_connections.py``): derived at run
time from the signed-in account and ``GET /api/orgs/{org}/providers/available``
(checked at sign-in and every 15 minutes), and **never written to
connections.json**. ``ConnectionStore`` refuses method ``TEAM`` and ignores a
hand-written one. Each has the stored records' fields; its id is a UUID
derived from the console, organization, person and mode, so a chat bound to it
finds it again after signing back in, while another person or organization
gets different connections and native contexts. ``connection_access.py``
(re-exported by ``ui``) merges them with the stored ones for chat,
collaborator, Media Studio and Flows lookups, and applies the managed policy
there: while it refuses personal connections they are not found at all, and
what already runs on them ends (``end_refused``, after every account change) once
the policy is known to forbid them; an account only offline ends nothing. A
chat on a mode's own legacy key is refused the same way (``mode_key_usable``).

The same providers answer may carry ``cacheAffinitySecret``: the team's 32
random bytes as 64 lowercase hexadecimal digits, which the console sends to the
app's token (``generate`` scope) of a member and never to a browser session.
``team_reads.cache_affinity_secret`` reads nothing else as a secret (an older
console sends none). The account keeps it in memory only, with the organization
and person it was read for, never in its view, a file or a log
(``TeamAccount.redact`` removes it). A console call whose token is refused
(401: a removed member's grant is revoked) drops it before the refresh, however
the refresh ends; a 403 or 404 answer, a sign-out or another person's sign-in
drops it too, and it is handed out only to a signed-in member with a seat of
that organization (the console also sends it only with a seat). It keys the team's Codex
prompt-cache routing (``gpt_launch.rst``, "Agent runtime and prompt caching",
Team chats); an administrator rotates it in the console, and members receive
the new one with their next providers read.

Registry modes (``provider_registry.py``, method ``TEAM``, derived from their
personal counterparts; ``service_mode`` gives the counterpart whose request
formats they use):

======================  ================  ==============================
Team mode               Gateway provider  Route under the gateway origin
======================  ================  ==============================
``claude_team``         anthropic         ``/v1/anthropic``
``codex_team``          openai            ``/v1/openai/v1``
``grok_team``           xai               ``/v1/xai/v1``
``minimax_team``        minimax           ``/v1/minimax/v1``
``kimi_team``           moonshot          ``/v1/moonshot/v1``
``openai_images_team``  openai            ``/v1/openai/v1``
``gemini_images_team``  google            ``/v1/google/v1beta``
``seedream_team``       byteplus          ``/v1/byteplus/api/v3``
``tripo_team``          tripo             ``/v1/tripo/v3``
``meshy_team``          meshy             ``/v1/meshy``
======================  ================  ==============================

A team connection appears when the organization offers its provider. It is
usable when the person holds a **seat**, the sign-in has the ``generate``
scope and, for coding agents, a current grant key exists. Without a seat AI
Providers shows one line and the administrator contact instead of the
connections; a provider the team doesn't offer says *Your team hasn't added a
key for <provider>. Contact your admin for access.* In a team or
policy-managed context every "no access" state ends in **Contact your admin
for access** (with the policy's ``AdminContact`` when set). The New connection
form never offers ``TEAM``.

* **Coding agents** (``team_runtime.py``). Claude Code gets
  ``ANTHROPIC_BASE_URL={gateway}/v1/anthropic`` and
  ``ANTHROPIC_API_KEY=<grant key>`` (sent as ``x-api-key``) in the
  connection's own profile, launched as a personal Claude connection is.
  ``child_environment`` strips an inherited ``ANTHROPIC_BASE_URL`` for every
  mode and sets it only for this managed case. Billed per token like a
  personal API key, a team chat writes the prompt cache at Claude Code's
  5-minute default there (``model_routing.rst``, "Prompt-cache TTL per
  session"). Codex gets a process-local
  ``model_providers.codex_team`` entry whose ``base_url`` is the gateway route,
  ``env_key`` ``ANGELO_TEAM_GRANT_KEY`` (sent as ``Authorization: Bearer``),
  ``wire_api`` responses and ``supports_websockets`` false. A team Codex mode
  also sets ``capabilities.request_compression`` ``"zstd"`` when the gateway
  serving it says it decodes zstd request bodies before forwarding
  (``team_runtime.request_compression``): its ``GET /health`` names
  ``requestEncodings``, asked once per gateway origin and launch with no key
  (``gateway_capabilities``; a gateway without the field, an error or no
  answer within 2 s leaves compression off and is asked again after 10
  minutes). The agent runtime then compresses each Responses request as stock
  Codex does for ChatGPT sign-in (level 3, every request); a new chat's
  opening request shrinks about 3.7x (108,031 B to 29,500 B). So the app and the gateway can be
  released in either order. A provider's own API is never sent a compressed
  body. Claude Code compresses
  (gzip) only toward ``api.anthropic.com`` itself: its check is on the request
  host, before any setting, so the team route stays uncompressed. Keys are never on
  argv or in a file. ``codex_team`` takes the personal OpenAI mode's path
  (``team_runtime.openai_service``): deferred tools and typed plans. It has no
  account catalog, so its models are pinned (GPT-6.1 Sol, the default, and
  GPT-6 Astra, efforts low/medium/high). Catalog and balance probes are never
  sent: the gateway refuses them.
* **Media Studio, Flows and AI Enhance** use the in-memory access token
  (``Authorization: Bearer``) against the provider's gateway route
  (``generative.bind_team_generation``, transport ``studio_http.py``). The
  binding asks the account for a current token for every request, so a long 3D
  job outlives the 10-minute token; asset and CDN downloads stay direct and
  unauthenticated, as for personal connections.
* **Gateway errors** (``team_errors.py``): ``UNAUTHENTICATED`` renews the
  access token once and resends that request (the gateway refused it before it
  reached the provider, so nothing was submitted or billed; the only automatic
  resend of a generation POST), then asks to sign in again; ``FORBIDDEN``
  names the seat; ``NO_PROVIDER_KEY`` names the provider; ``RATE_LIMITED``
  respects ``Retry-After`` (a limited status poll waits and continues the
  submitted task); ``UNAVAILABLE`` is retryable, never a sign-out. A
  provider's own error passes through and is shown as one about the team's
  key.

**The organization's app policy** arrives with every token answer as
``policy``, always with all five fields (``allowPersonalConnections``,
``allowLocalCliAccess``, ``allowVoice``, ``allowCollaborators``, each
defaulting to true, and ``adminContact``, text or null; ``allowAutoApproval`` and
``requireAskForEdits`` count only when sent, which the console doesn't yet:
``enterprise_policy.rst``), so each 10-minute
renewal carries the current one; the app doesn't call
``GET /api/orgs/{orgId}/app-policy``. ``team_view.org_policy`` keeps a missing
switch at its default and reads a non-boolean one as off; it shows
``adminContact`` by the console's own rule (one line of at most 200
characters without control characters, line separators or bidirectional
overrides) and the machine policy's ``AdminContact`` is read the same way. ``managed_policy.current`` combines it with the
machine policy, the stricter value winning, the machine's admin contact first;
only the machine policy pins where to sign in. The last policy received is
kept beside the connections (``team_org_policy.py``) and applies while a
stored sign-in restores or Angelo is offline; signing out forgets it. A
console without ``policy`` leaves the machine policy alone.

**The chat** (``team_chat.py``): the setup line under *Set up AI provider*
says the team's state (finishing in the browser, restoring, no seat or no keys
with "Contact your admin for access", the reason a sign-in ended), and
``welcome_row`` is the first-run connect card's "Your team" row
(``first_run.connect_options(team=…)``; signed out: *Sign in with your
organization account*, *Finish signing in in your browser*, *Restoring…*;
``welcome_action`` handles its click).

Signed in, the row and the AI Providers team section give the same reason
(``team_access.py``), from the state the
account already has: the seat and the role from the access token (its
``role`` claim, read without verification and only to choose what to suggest;
the console enforces permissions), the providers the team offers, and the
organization's ``adminContact``:

* *No seat in <team>*;
* *<team> has no AI provider keys yet*;
* *No coding provider (Claude or OpenAI) is set up for this team*.

An owner or admin is offered the console page that fixes it (*Assign yourself a
seat in People*, *Add keys in the console → Keys*; the row's click and an AI
Providers button open ``/orgs/<org>/people`` or ``/orgs/<org>/keys``); a member
is asked to contact their admin, with the contact when the policy sets one.
While the providers are being read or the device's gateway key is being
issued, the row runs with a spinner.

The providers are re-read without a restart: when focus comes back to a window
(the chat's input handler sees ``WINDOW_DEACTIVATE``, then the next event),
when a chat opens, at most every 30 seconds for those two
(``team_chat.RECHECK_AFTER``), and after every token refresh; each answer
redraws the chat and AI Providers at once.

Once a team coding connection is usable there is no row: a chat with no
connection, or one whose team connection stopped working or went away, starts
on the team's (``team_chat.bind_team_chats``; Claude first when the team
offers Claude and OpenAI), and the empty chat shows *Signed in to <team>. Your
team's Claude and OpenAI keys are ready.* in a teal line, the example prompts,
and *More options* (AI Providers) only where the policy allows personal
connections. The model pill names the team connection until a model is
chosen. A chat on a personal connection is left alone, and a new chat keeps
the conversation's choice, as always. ``test_blender_team_chat.py`` shows every
case.

All team and policy text is translatable: sentences are ``ui_text.n_``
templates with named fields, carried as ``display_text.Text`` (an English
``str`` that remembers its template) and translated where drawn.

Account in the status bar
-------------------------

``team_status_bar.py`` appends an item to
Blender's status bar right of the version number: *<team> · <name>* when
signed in (a warning icon without a seat), *Signing in…*, *Team offline*, or
*Sign in* (a warning icon when the policy allows only team connections). Its
menu shows the account, team, seat and this period's usage
(``GET /api/orgs/{org}/usage/people/{user}``: tokens, agent tasks,
generations, active days and streak; costs are not shown), opens **Profile
and Usage**, **Your Devices** and **Team Library** in the console, **AI
Providers**, and **Sign Out** (or Sign in). Opening the menu refreshes the
numbers at most once a minute. The status bar is a global area: Python can't
tag it, so the account's changes redraw it through the message bus
(``Window.view_layer``, which it subscribes to). It is an emboss-less
``wm.call_menu`` button because header menus are sized without room for an
icon and text.

Agent usage reports
-------------------

The gateway meters tokens and cost for the console's Usage page, but only the
app knows what an agent task is, so it counts tasks locally and reports them
in batches with ``POST /api/orgs/{org}/usage/app-reports`` (the bearer access
token, scope ``api``; the console's ``appUsageReportBody``)::

    {"batchId": "<a UUID made once per batch>",
     "entries": [{"day": "2026-10-10", "provider": "anthropic", "model": "claude-opus-5-5",
                  "agentTasks": 3, "longestTaskMs": 95000}]}

One entry per UTC day, gateway provider and model, at most 200 per report;
the answer is ``{"applied", "skipped"}``. Tokens and cost are never sent,
and the console refuses unknown fields.

* **What counts** (``team_usage_reports.task_entry``). One completed agent
  request (``Controller._finish_metrics`` with status ``completed``) on a team
  coding connection: ``claude_team``, ``codex_team``, ``grok_team``,
  ``minimax_team`` or ``kimi_team``, under the gateway provider of the table
  above. Its length is the request's ``total_ms`` receipt phase, from send to
  idle, approvals included. The model is the one the runtime served, as the
  gateway records it: the requested model when it was served (or nothing was
  reported), else the strongest known served model (Claude Code may also call
  a fast model for chores), else ``unknown``. Personal connections are the
  person's own accounts, not the organization's usage, and are never counted;
  nor are failed, stopped or escalated requests (an Auto escalation's re-run is
  the task), local preview acceptances (no route) or collaborator
  consultations (part of their request). Only the receipt's allowlisted route
  and phases are read: no prompt, path, token count or cost.
* **Counting** does no I/O. The controller's one-line hook
  (``team_usage_reports.count_task``, attached by ``team_account_ui.start``)
  adds the task in memory for the signed-in person (console, organization,
  user) and queues a save on the account's worker. Signed out, or restoring a
  sign-in that hasn't answered yet, nothing is counted.
* **The buffer** (``team_usage_buffer.py``) is ``team-usage.json`` in the
  connections folder, shared by every Angelo process of the profile. Per
  person it keeps the ``counts`` per UTC day, provider and model, at most one
  ``batch`` (the report body, sent verbatim), ``hold_until`` and ``next_at``.
  Every change is a read-modify-write under ``team-usage.lock`` (``team_lock``:
  an OS file lock, released when a process dies; its own file, so a save never
  waits behind a token refresh's network call) ending in an atomic replace, so
  a crash leaves the previous complete file. A batch is formed under that lock
  from up to 200 entries and keeps its ``batchId`` until a 2xx; counts that
  arrive later go to the next batch. So two processes never report the same
  counts under different ids, and a resent batch is answered
  ``applied: false``. Days older than 30 or more than one ahead are pruned
  (the console would skip them), as is a batch with nothing left to count.
  Each person keeps at most 1,000 keys and the file at most four people. No
  token, prompt or path is written; a damaged file reads as empty.
* **Reporting** runs on the account's worker every 15 minutes while signed in,
  first right after sign-in or start, which sends what an offline or quit
  session left. A process skips its turn when another process of the profile
  delivered a report less than 14 minutes ago (``next_at``), so a person sends
  about four reports an hour whatever the number of windows (the console
  allows 60). Up to three batches go back to back when more than 200 entries
  wait. A batch being sent is leased for 30 seconds so that other processes
  leave it alone. A 2xx removes the batch. A 429 holds every process until
  ``Retry-After`` (15 minutes without one). A 400 drops the batch and logs one
  fixed line (``Angelo: the console refused a usage report as malformed``);
  403 and 404 drop it, since it can never land there. A 401 renews the access
  token once, as every console call does (``TeamAccount._authorized``). Other
  statuses and network failures keep the batch for the next report. Nothing
  is ever shown to the person.
* **At quit** (``TeamAccount.close``, which Blender's exit reaches through
  the add-on's unregister) the counts are saved and one batch is sent with the
  access token already held (no refresh at quit), on a daemon thread that the
  quit waits for at most one second (``EXIT_SECONDS``). A batch still in
  flight stays in the file with its id and goes again at the next start.
* **Policy.** Neither the machine policy (``managed_policy.KEYS``) nor the
  organization's app policy has a switch for telemetry or usage reports, so
  none applies. Reports go only to the console the person signed in to (the
  one a managed ``ConsoleURL`` pins), for that organization's own Usage page,
  and only while signed in.

``test_native_team_usage_reports`` covers the task rule, the buffer (batch
ids, the 200-entry cap, the day window, its bounds, a real process killed
mid-write) and the console's body rules. ``test_native_team_usage_flush``
runs the account against the fake console: one batch, the 15-minute schedule,
a lost answer resent with the same id and counted once, 400, 403 and 404,
429 and ``Retry-After`` (also the console's own limit), a refused token
renewed once, quit within its second, a slow console leaving the batch for the
next start, quit never renewing a token, signed out sending nothing, the
controller hook, and four real processes sharing one profile (every task
reported exactly once, no refresh token reused). ``test_blender_team_account``
reports a completed ``claude_team`` request through the compiled app's
controller.

How to continue (usage reports)
-------------------------------

* A new team coding mode is counted without a change (``TEAM_CODING`` comes
  from ``provider_registry``). Keep its ``team_provider`` equal to the
  gateway's provider id, so its tasks and tokens land in the same console row.
* Generations (Media Studio, Flows, AI Enhance) are counted by the gateway;
  they are not part of app reports.
* If a usage-report switch is added, add it to the console's app policy and
  the machine policy together (``team_view.ORG_SWITCHES``,
  ``managed_policy.ORG_FIELDS`` and ``KEYS``, next to
  ``DisableCrashReporting``), stop ``count_agent_task`` while it is off and
  drop that person's buffer.
* ``fake_usage_reports.py`` follows ``appUsageReportBody`` and the route's
  middleware order (token, organization, rate limit, body); when the contract
  changes, change both, then this section.
* Not run yet: a live check against a real console. Sign in a dedicated test
  profile to a test organization, finish one team task, quit, and look for it
  on the console's Usage page.

Gateway compatibility
---------------------

``team_gateway_probe.py`` (unbilled) launches the installed runtimes exactly as
the team modes do against a local recorder laid out like the gateway and
checks every request against the gateway's allow-list. Results with these
runtime versions:

* **Codex** 0.162.0-alpha.2 (``codex_team``): only
  ``POST /v1/openai/v1/responses`` with ``Authorization: Bearer``, for turns
  and for compaction (``thread/compact/start`` summarizes through
  ``/responses``; ``/responses/compact`` is used only for OpenAI's own
  provider). No ``/models``, no WebSocket (``supports_websockets`` false), no
  file or realtime path. Voice (realtime) stays unavailable on team
  connections.
* **Claude Code** 2.1.293 (``claude_team``, launched by ``claude_session``
  with WebFetch and WebSearch): only ``POST /v1/anthropic/v1/messages?beta=true``
  with ``x-api-key``. That covers the gateway only: Claude Code's non-essential
  traffic goes to ``api.anthropic.com`` directly. By Claude Code's code (not yet
  observed live), a web search is one more request on the gateway path and
  WebFetch's domain check also goes to ``api.anthropic.com`` directly
  (``agent_chat.rst``, "Web and file tools").

Every path is on the allow-list; nothing needs a gateway change.

Live verification
-----------------

``live_team_sign_in.py`` (no provider or gateway model calls; a person clicks
Allow) runs the app's team modules against a real console with the real OS
store and a fresh profile. Against the default console, with the macOS
Keychain, it showed: sign-in (an EdDSA access token whose ``kid`` is
published, ``iss`` the console, ``aud`` angelo-api and angelo-gateway, 600 s);
``providers/available``; a refresh shared by four concurrent callers (one
rotation); two other processes restoring the same Keychain sign-in at once
(both signed in, nothing revoked, the first process adopted their token); the
grant-key refusal without a seat (*You don't have a seat in this Angelo team.
Contact your admin for access.*); sign-out (revoked: a later refresh is
refused; both Keychain items gone). The token answer carried ``policy`` with
all five fields, equal to ``GET /api/orgs/{orgId}/app-policy``, and the app
applied it and kept it for offline use; signing the computer out from Your
devices ended the sign-in at the next renewal with ``error_reason``
``signed_out`` (*This computer was signed out of your Angelo team*), both
Keychain items and the kept policy gone; the next sign-in link carried
``login_hint`` (the ended account) and ``org_hint`` (its organization), and an
Allow on it signed in to that organization. The console's sign-in step filled
in a ``login_hint`` address and ignored malformed hints. Grant-key issue and
gateway calls have not been run live (the test organization had no seat for
the person and no provider keys). Outside the app the script uses certifi
directly, because Blender's Python has no CA roots of its own (see *Trust*
above).

Team gate route
---------------

``eval_live_agent.py --team-mode claude_team|codex_team`` (and
``bench_mcp_vs_angelo.py --angelo-team-mode``) run the live evaluation gate
through the gateway: ``--source-connections-dir`` is a profile dedicated to
the team (no stored connection), where someone signed in to the team **with
the same Angelo build that runs the gate** (a Keychain item belongs to its
app); each trial restores that sign-in in place, waits up to 90 seconds for
the team connection to be usable (grant key included) and binds it. Every
token is billed to the organization's own provider key, and each completed
trial request is also reported to that organization's console as an agent
task (`Agent usage reports`_). An unbilled dry run
against the fake console and a local gateway recorder sent all six tasks of
the default set through each runtime (one grant key and one refresh in all,
every request on the allow-list).

Fake console
------------

``fake_team_console.py`` follows the console's behaviour: the allow
screen's request checks and its redirect with ``iss`` (recording ``org_hint``
and ``login_hint``); single-use 60-second codes whose reuse revokes the grant;
rotating refresh tokens whose reuse revokes the grant, optionally expiring
(``refresh_lifetime``); membership re-checked on refresh; every
``invalid_grant`` a 400 with the console's description and, when
``error_reasons`` is on, its ``error_reason``, a revoked grant's reason mapped
as the console maps it; ``policy`` in token answers when set
(``app_policy`` fills it the way the console does); revoke; grant keys (``generate`` scope, the seat read now, at
most 8 per grant); providers/available, with ``cacheAffinitySecret`` for a
``generate`` token when ``cache_affinity_secret`` is set, and the person's usage (404 to
non-members and other organizations); the app's usage reports
(``fake_usage_reports.py``: the body checked as ``appUsageReportBody`` reads
it, batch ids applied once, the day window, 60 reports an hour with
``Retry-After``; see `Agent usage reports`_); UUID ids and JWT-shaped access
tokens with a published ``kid``. Deliberate differences: the access token's
signature is random bytes (the app never verifies it; the gateway does);
grant-key issue ignores the request body, as the console does, but tests
assert that the app sends JSON; the person's usage is a fixed fixture and
does not include reported tasks; there are no admins, roles beyond
``member``, other rate limits, MCP clients or audit log.

The app also works with a console that sends no ``policy`` or ``error_reason``
and ignores ``org_hint`` and ``login_hint``
(``test_native_team_console_contract.py`` runs each shape both ways).

Not yet
-------

* HTTP proxies for console and gateway requests (corporate networks that only
  allow a proxy can't sign in). See ``doc/angelo/enterprise_policy.rst``.
* A Windows run of the Credential Manager items, the team lock and the policy
  registry reader (``doc/angelo/enterprise_policy.rst``, "What needs a Windows run").

Coding API adapters
===================

All three adapters use official Codex app-server and Responses wire format.
There is no protocol conversion, local proxy, OAuth token extraction or custom
model/tool agent loop. Config overrides are process-local; keys are in explicit
child environment variables, never CLI arguments or config files. Each account
uses its own CODEX_HOME. OpenAI account/login RPCs and OpenAI's model catalog are
not used for these providers. Team Codex connections (`Team account and team
connections`_) use the same adapter: their ``model_providers`` entry's base URL
is the provider's route on the organization's Angelo gateway and its key, a
grant key, is in ``ANGELO_TEAM_GRANT_KEY``.

================== ======================= ==================================
Provider           Admitted model          Official base URL
================== ======================= ==================================
Grok / xAI         grok-4.6                https://api.x.ai/v1
MiniMax            MiniMax-M3              https://api.minimax.io/v1
Kimi / Moonshot    kimi-k3                 https://api.moonshot.ai/v1
================== ======================= ==================================

GET /models checks the supplied key without inference. Only compatible admitted
IDs returned by that key's catalog are selectable. The registry is deliberately
conservative; future models require explicit capability admission, not guessing
from their names. Reasoning: Grok low/medium/high/xhigh; Kimi low/high/max;
MiniMax M3 none/high (off/on, not a fictitious graduated reasoning slider).
MiniMax's 1M and Kimi's 1,048,576 context windows are configured explicitly.

Grok/Kimi enable native web search. MiniMax search isn't enabled through this
adapter. Voice remains restricted to supported OpenAI/Codex modes. Fast is not
advertised for these providers, whose native tier mapping is not validated,
even where the API documents a paid priority tier. No unsupported microphone/Fast
control can silently bill another connection.

Native shell, files, image tools, hooks, apps/plugins, computer use, persisted
goals and native multi-agent v1/v2 tools are disabled. Only Angelo's host-approved
Blender/file tools and bounded, usage-approved collaborator tool can act on the
document. The actual runtime request was inspected to confirm the multi-agent
namespace and raw file/image tools are absent; configuration intent alone is
not evidence of this boundary. Native clarification requests are rejected
(there is no host handler for them); ordinary chat questions are supported.

Generation service bindings
===========================

==================== ================================== ==========================================
Service              Model ID                           Connection check
==================== ================================== ==========================================
OpenAI Images        gpt-image-2.5-flare                GET https://api.openai.com/v1/models
                     gpt-image-2.5-sunburst             Same image account
Gemini Images        gemini-nano-banana-2.1             GET /v1beta/models?pageSize=1000
                     gemini-3.1-flash-image             x-goog-api-key header, never query-string key
                     gemini-3-pro-image                 Same image account
BytePlus Seedream    dola-seedream-5-0-pro-260628       Deferred; no generation probe
Tripo 3D             P2-20260801                        GET https://openapi.tripo3d.ai/v3/account/balance
                     tripo-v3.1                         Same account
Meshy 3D             meshy-7                            GET https://api.meshy.ai/openapi/v1/balance
                     meshy-7.1                          Same account
==================== ================================== ==========================================

Nano Banana 2.1 (the default when the key lists it), Nano Banana 2 and Nano
Banana Pro are the Gemini display names. Seedream uses the
official BytePlus Singapore endpoint; a different regional service/key is not
silently substituted. All other auth headers are Authorization: Bearer.

Registry versions are pinned, not mutable latest aliases. Tripo's current model
page still lists P1 whereas its newer August changelog documents P2-20260801;
the P2 entry follows that changelog. Tripo 3.1 remains its documented high-detail
default. Meshy 7 remains available but is deprecated in favor of 7.1; both are
kept as explicit options, so a chosen 7 is never silently replaced with a newer
model.

A successful catalog or balance check verifies only that operation. It doesn't
guarantee generation-model entitlement, org verification, credit sufficiency or
output quality. Tripo/Meshy model access remains labeled not_checked. Seedream
is labeled configured/unverified, never Connected. GenerationBinding resolves
only the exact chosen service/model to documented operation endpoints and
credential headers; creating it makes no network/billable request. The Media
Studio workflow built on these bindings is in ``generative_studio.rst``. A team
binding resolves the same routes under the organization's gateway with a
Bearer access token (`Team account and team connections`_); team connections
send no connection check.

Validation
==========

* Saved keys: ``test_native_saved_keys.py`` and ``test_native_saved_keys_ui.py``
  (any host OS, fake framework/backend) pin query shapes, prompt policy,
  deadlines, profile isolation, typed-key parity, per-platform labels, the
  probe's switch to memory-only and operator contracts.
  ``test_native_key_store.py`` pins the policy, the disable switch on every
  platform and backend selection. ``test_native_credential_manager.py`` and
  ``test_native_secret_service.py`` run the real Windows and Linux bindings
  against fake advapi32 and libsecret/GLib libraries (``fake_c_libraries.py``),
  called both directly and through C function pointers with the bindings'
  declared signatures, so ctypes converts every argument as it would for the
  real library: item identity, UTF-8, empty and oversized keys, buffer
  zeroing, reference and GError release, locked keyrings that automatic calls
  never unlock, dismissed and unanswered prompts, missing libraries, services
  and vaults, and the shared worker on each backend. The Linux suites also pin
  the KDE reasons and session detection, and ``test_native_key_store.py``
  reads sample ``/.flatpak-info`` files (bus policy, wildcards, the
  ``session-bus`` socket, unreadable files) with the real ``configparser``.
  Neither backend has run on real Windows or Linux (see the manual checks and
  `How to continue (saved keys)`_).
  ``test_blender_saved_keys.py`` (BACKGROUND) drives the real operators,
  restart, Load Saved Key, removal and a blocked Keychain with a fake backend. ``test_blender_keychain_live.py`` (LIVE host driver, needs
  ``ANGELO_QA_LIVE_KEYCHAIN=1``) round-trips throwaway test-only items through
  the real Security framework with prompts disallowed, including an item
  created by host Python and then read, replaced and deleted by the ad-hoc
  signed app (the rebuild path), and deletes everything it created.
* Team account (any host, no real console or gateway):
  ``test_native_team_signin.py`` drives the real loopback listener (127.0.0.1
  only, alive only during a sign-in, wrong path/state refused, timeout and
  cancel close it, nothing logged) and PKCE; ``test_native_team_account.py``
  signs in through it against an in-process fake console
  (``fake_team_console.py``): code exchange, refresh token only in the store,
  single-flight rotation with eight concurrent callers, no token presented
  twice after a lost answer, unprocessed refusals and offline keep it,
  adoption of another instance's rotated token, revoke and erase on sign-out,
  late results revoked, grant-key renewal policy, ``api_get`` refresh-once,
  and the ``Angelo account`` item through a fake backend;
  ``test_native_team_connections.py`` pins the registry routes, virtual
  records never written to connections.json, the child environment for team
  and personal connections, Codex ``model_providers`` for team modes, gateway
  bindings and headers per service, error mapping, loopback-only http to a
  configured gateway, the UNAUTHENTICATED resend and RATE_LIMITED polling;
  ``test_native_team_account_ui.py`` pins the main-thread glue (grant key
  mirrored only for usable coding connections, idle-only renewal, sign-out
  withdrawal), the ``ui.py`` routing and the modules' size, license, bpy-free
  core and single listener. Not run: the browser allow screen, a real console
  or gateway, real Keychain/Credential Manager/keyring items of the
  ``Angelo account`` kind, and Claude Code or Codex through the gateway.
* Native sign-in suggestions: ``test_native_suggestions.py`` (any host) runs the
  store, rules and the actual ``ui.py`` form, dismiss, draw and unbound-chat
  routing functions: detection adds and binds nothing, the prefilled form adds
  one ``reuse_native`` connection only on confirmation, dismissal survives
  reload, removal and a stale instance, earlier records are kept and an
  isolated store offers nothing. ``test_blender_native_suggestions.py`` (GUI,
  event simulation) repeats it in the app with a stand-in status: the rows,
  the chat hint, the real form cancelled with Esc and confirmed with Return,
  Dismiss, removal and re-detection, with screenshots.
* Connect on every surface alike: ``test_native_connect_offers.py`` (any host)
  checks that AI Providers offers what the welcome card offers, in its order and
  words, creates nothing, honours a missing runtime, a dismissal and an existing
  connection, and fits at 1100, 520 and 380 px. ``test_blender_connect_surfaces.py``
  (GUI, stand-in CLIs: ChatGPT signed in, Claude Code signed out) captures the
  showcase and welcome card, the welcome screen, AI Providers, its form for
  OpenAI and Anthropic, and a Connect Claude click on the page that ends with
  one Claude connection (private profile) while ChatGPT is still offered.
* Pure Python tests: roles/migration, key isolation, catalog filtering, model
  groups/default alias/search, header auth, no redirects/inference, error
  redaction, fractional balances, and stale async check invalidation.
* Real Blender settings/form/picker test: 15 checks with fixture keys/catalogs;
  five coding providers in one picker, five distinct generation accounts, no
  serialized credentials, shared-chat routing, searchable groups, scene unchanged.
* Existing native picker regression: 21 checks including normal-click approval,
  model selection, native animation/reduced motion and paid Fast confirmation.
* Actual official Codex runtime -> localhost Responses fixture: all three
  providers authenticate with the synthetic selected key, create native threads,
  call the host's scene-inspection tool and submit its result on the next model
  request. No paid requests. Actual emitted tools and service tier inspected.
* Real Blender TLS/endpoint smoke: all seven catalog/balance endpoints reachable
  with native TLS verification. No credentials were sent; each returned the
  expected unauthenticated 401/403. This is not account authentication evidence.

Apart from the single live Gemini Images and Tripo 3D generations recorded in
``generative_studio.rst``, no live generation, remote provider entitlement or
production latency was tested. Saved-key prompts on a rebuilt or developer-signed app
were not exercised by an automated run, and the Windows and Linux stores have
not run on those systems. Credentials must be supplied through
the password field, not pasted in chat.

Primary sources
===============

* `OpenAI image generation <https://developers.openai.com/api/docs/guides/image-generation>`__
* `Gemini image generation <https://ai.google.dev/gemini-api/docs/image-generation>`__
* `Gemini Nano Banana 2.1 <https://ai.google.dev/gemini-api/docs/models/gemini-nano-banana-2.1>`__
* `Gemini model listing <https://ai.google.dev/api/models>`__
* `Seedream 5.0 Pro <https://docs.byteplus.com/en/docs/modelark/seedream-5-0-pro>`__
* `Tripo changelog (P2) <https://developers.tripo3d.ai/en/docs/changelog>`__
* `Tripo model versions <https://developers.tripo3d.ai/en/docs/models-and-versions>`__
* `Tripo account balance <https://developers.tripo3d.ai/en/docs/account>`__
* `Meshy changelog <https://docs.meshy.ai/en/api/changelog>`__
* `Meshy balance <https://docs.meshy.ai/en/api/balance>`__
* `Grok 4.6 <https://docs.x.ai/developers/models/grok-4.6>`__
* `Grok Responses <https://docs.x.ai/developers/rest-api-reference/inference/responses>`__
* `MiniMax Responses <https://platform.minimax.io/docs/api-reference/responses-create>`__
* `MiniMax models <https://platform.minimax.io/docs/api-reference/models/openai/list-models>`__
* `Kimi K3 in Codex <https://platform.kimi.ai/docs/guide/codex-kimi>`__
* `Kimi Responses <https://platform.kimi.ai/docs/api/responses>`__
* `Codex configuration <https://learn.chatgpt.com/docs/config-file/config-reference>`__
