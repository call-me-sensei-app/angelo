.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Angelo's MCP server for external AI apps
****************************************

External AI apps (Claude Code, Codex, Claude Desktop, Cursor, VS Code or any
other MCP client) can work with the open Angelo document at two levels:

* **Tools.** The app's own model calls Angelo's typed tools: scene reads,
  views, bounded typed edits, background jobs and reviewed Python. Angelo runs
  no model; the app pays for its own.
* **Tasks.** The app hands Angelo's own agent a task in plain language. It runs
  as a turn in the app's External conversation, on that conversation's
  connection (the person's subscription unless they chose another), and the app
  reads its progress and result.

Without Angelo's window, an app can also open a ``.blend`` headless: reads,
views and bounded typed edits, each edit saved to a new file beside the source
(`Headless: a .blend without Angelo's window`_).

Either way the person stays in charge in Angelo: approvals, Stop, snapshots and
Undo are the in-app ones, and an app can never approve, decline or change a
mode. macOS (and Linux) only for now: Windows needs the named-pipe transport
(``external_cli.rst``, "Later phases").

Blender Lab's own MCP add-on ("Blender MCP") is separate and unchanged; see
`Blender MCP`_.

Architecture
============

::

   MCP client --stdio--> angelo_mcp.py (bundled Python -I; external_mcp.Server, stdlib only)
     --> the local CLI socket (external_cli: peer UID, launch token + this app's key)
       --> RequestService (allow-list by the app's levels, bounds, rate) --> WorkQueue
         --> ExternalHost.drain in ui.pump (main thread)
             tools: external_apps.handle_app_call --> external_work.ExternalWork
                    --> claude_direct.DirectRequest --> Controller.request_tool / pump_tools
             tasks: external_apps.handle_task --> Controller.send_external_task (a normal turn)

=============================== ==================================================
Module                          Responsibility
=============================== ==================================================
``external_mcp.py``             The stdio MCP server (launcher): JSON-RPC, tools,
                                repeated asks for a waiting call, progress
``external_mcp_tools.py``       Tools offered per level, task tools, the server's
                                instructions
``external_clients.py``         Connected apps: registry, per-app keys, levels
``external_work.py``            One open request of an app's tool calls
``external_apps.py``            Main-thread handling of app calls and tasks
``external_setup.py``           Adding Angelo to an app's MCP settings
``external_ui.py``              Connect, Disconnect, Copy setup, the status-bar
                                notice of an app waiting for approval
``providers_page_external.py``  The Preferences card, as data
``headless_session.py``         Launcher: the session's headless child, restarts,
                                audit
``headless_pipe.py``            The child's two inherited pipes (both ends)
``headless_files.py``           Which .blend opens, changes on disk, the output
                                file's name and claim
``headless_main.py``            Child start-up inside ``Angelo --background``
``headless_host.py``            Child: ``ExternalHost`` over the pipes, its loop,
                                ``needs_gui``, saving and reloading
=============================== ==================================================

The server's ``instructions`` state the host's contract as facts and include
the Blender API facts every route whose model writes scripts reads
(``agent_guidance.BLENDER_API_FACTS``, the one shared statement; an app's
model runs Python through Angelo too). The prompt audit's registry
(``prompt_audit.rst``) requires them on the MCP routes.

The transport is the CLI's (``external_cli.rst``): a private Unix socket, a
per-launch token proved with HMAC, a kernel peer-UID check and one framed
request per connection. It opens no TCP, UDP or HTTP listener, so it never takes
a port. The launcher writes only JSON-RPC to stdout (anything stray goes to
stderr) and starts in about 0.25 s (it builds the tool list itself, so a client
that starts before Angelo still sees its tools).

Connecting an app
=================

Preferences > AI Providers > **External AI apps (MCP)** lists Claude Code and
Codex, each with a button that names the app ("Connect Claude Code", "Connect
Codex"; "Copy setup" when the app is not found). A click:

1. records the app in ``<state>/mcp/clients.json`` (owner-only) under its
   client name (``claude-code``, ``codex``) and writes a fresh 32-byte key to
   ``<state>/mcp/keys/<name>`` (``0600``, directory ``0700``);
2. adds one ``angelo`` server entry to that app's settings (below), which runs
   ``<bundled python> -I <scripts>/angelo/angelo_mcp.py --client <name>
   --state-dir <state>``;
3. turns on **Allow apps on this computer to reach Angelo** (the CLI's switch).

=============== ======================================================================
App             Where the entry goes
=============== ======================================================================
Claude Code     ``claude mcp add --scope user angelo -- <command>`` (its own CLI)
Codex           ``$CODEX_HOME/config.toml`` (``~/.codex``), ``[mcp_servers.angelo]``
                with ``tool_timeout_sec = 900`` and ``startup_timeout_sec = 30``
=============== ======================================================================

``external_setup`` also knows Claude Desktop, Cursor and VS Code settings
(tested), but the card offers only the two apps above for now. Any MCP client
can use Angelo with the snippet **Copy setup** gives.

**Codex loads Angelo's tools on demand.** Claude Code gives its model every
tool and the server's instructions with the first request. Codex 0.163 decides
by model: GPT-6 models run in code mode (one JavaScript ``exec`` tool, other
tools listed in ``ALL_TOOLS`` only when the model looks), GPT-5.5 finds them
through tool search, and older models get them up front. Codex has no channel
for a server's instructions; it prefixes them to each Angelo tool's
description, so the model reads them, Blender facts included, when it looks an
Angelo tool up. Its calls then work as any app's. So with Codex, a request
should mention Angelo or Blender ("in my Angelo scene ..."), or the model may
not look for Angelo's tools at all. Measured unbilled against a stand-in model
on 2026-10-21 (Codex 0.163.0); the same holds for any MCP server, Blender MCP's
included.

Only the ``angelo`` entry is ever written or removed; every other server
(Blender MCP's included) and setting is kept. An edited file is replaced
atomically with its previous content kept as ``<file>.angelo-backup``. A file
Angelo cannot write back exactly (JSON with comments, duplicate keys or numbers
out of range, a Codex ``angelo`` entry in inline-table form, or any Codex edit
that would change anything but Angelo's entry once parsed) is never rewritten; a
settings file that is a link is written through it: the card says so and offers **Copy
setup** to paste by hand. A Codex file is re-parsed before it is written and
must then hold exactly Angelo's entry. The key never goes into an app's
settings, which an editor may sync or a dotfile repository may hold; the
launcher reads it from Angelo's state.

**Disconnect** deletes the key and the registry record, and removes the entry.
A client still configured with the old entry then gets "This app was
disconnected in Angelo"; a reconnect rotates the key.

Authentication and levels
-------------------------

The handshake adds one optional field to the CLI's: ``app_proof`` =
HMAC-SHA256(app key, ``angelo-mcp/1/app``, server nonce, client nonce). A
connected app's name is reserved: a hello under it must carry a valid proof, and
a proof for a name Angelo no longer knows is refused. A label that is not a
connected app stays a phase-1 CLI client (read-only). The threat model is the
CLI's (same user, local machine): a same-user process can read the key too, so
it buys revocation, attribution and no accidental use; approvals in Angelo stay
the boundary.

Each app has levels (``external_clients``); the card connects an app with all of
them, and per-level switches are not offered yet:

========= ===================================================================
Level     Tools
========= ===================================================================
read      Always: scene inspection, search, objects, relationships, delta,
          verify, settings, ``read_data``, ``blend_file`` (workspace-relative),
          previews, recipes, API lookups, capabilities, metrics, the brief,
          scripts sent, views (``angelo_view``, sculpt views), ``job_cancel``
edit      ``BOUNDED_MUTATIONS`` (programs, batches, recipes, transforms,
          previews, sculpt tools) and ``angelo_job_start`` (renders, bakes,
          exports)
python    ``angelo_execute_python``, always with exact-code review, and
          ``angelo_run_script_file`` (`Script files`_)
tasks     ``angelo_task``, ``angelo_task_status``, ``angelo_task_stop``
========= ===================================================================

Never available to an app: file write, delete, list and read, task-memory
updates, workspace mode, Media Studio and the team library, Generation Flows
and ``angelo_delegate``. ``RequestService`` refuses a call outside the app's
levels before anything is queued, and the app's ``tools/list`` shows only its
levels' tools. A task an app starts runs without those families too
(``RuntimeContext.begin`` narrows ``allowed_tools``; ``request_context_tool``
refuses any call outside a request's ``allowed_tools``), so nothing an app
asks for reaches them through Angelo's agent either.

Tool level
==========

Each app has one ``External: <name>`` conversation per open document, created
like a new chat (Approve for me; ``RequireAskForEdits`` makes it Ask). A CLI
label without an app key still gets Ask and the phase-1 read-only allowlist, and
a keyed app off the MCP path does too. An app's
calls run in **requests** there (``ExternalWork``):

* A request opens at the app's first call, with a user row naming the app
  (``request_kind: external_request``), and stays open while calls keep coming.
* It never snapshots before a read-only call. Before its first approved edit the
  pump takes the person's snapshot (their retention settings; only the moment is
  "before the first approved edit"), bound to that row, as a chat request's is.
* Each call is one operation through the direct-tools route's host code
  (``agent_integration.rst``, "Direct tools"): exact validation with the needed
  schemas returned on a rejection, ``run_operations`` with visual evidence and
  completions, kept views refreshed after an edit, the plan route's result
  presentation and short identifiers (``o<n>`` handles, kept in memory per
  conversation). Approval, Undo, journaling, invocation guards, receipts and
  script consent are ``Controller.request_tool``'s. Calls of one app run in
  arrival order. A call delivered again under its ID never runs twice: the host
remembers each app's last 1,024 call IDs with the request that ran them, so a
delivery after that request ended (Stop, a decline, a file change) gets its
first answer, or "did not run", from that request.
* Views come back as MCP image content; absolute private paths are replaced by
  placeholders, as in the CLI. Without the window, what the app named itself (the
  opened file's folder, where the output goes, and a script file it names) is
  shown as named, ahead of any broader placeholder such as ``<temp>`` or
  ``<home>`` (``headless_working_copy.named_roots``; the longest match wins). A
  project under the home or temporary folder still reads "Saved as" its real
  path, and a refusal names the script the app has to approve. (Linux CI,
  2026-10-24.)
* A request closes after ``external_work.IDLE_SECONDS`` (120 s) without a call
  and with nothing in flight, at Stop, when a call ends it (a decline, a failed
  edit that may have changed the scene: the direct route's rules) or on a file
  change. Its transcript then gets the host's verified account of its edits.
  The next call opens a new request.
* After a file is opened, the app's next call moves it to a fresh External
  conversation automatically (a model has no ``angelo session new``); a call that
  was running when the file changed says so.

While a call waits for the person (an approval card) or a job, the launcher asks
Angelo again under the same call ID every 25 s, sending
``notifications/progress`` when the client gave a progress token, for up to
15 minutes. ``notifications/cancelled`` only stops the waiting; the work in
Angelo continues until the person's Stop.

**One call, one run.** A call keeps its identity (its call ID) across every
round, in Angelo's window and without it alike: asking again about a call Angelo
has, or has finished, returns its pending or final result and never runs it
again. Once Angelo answered that it has the call, its answer is ``ok`` or
``stale``; anything else the launcher hears is about the re-ask, never the call
(``Server.call``):

* a round Angelo's main thread cannot pick up in time (a long script holds it)
  means the call is still running, never "not admitted";
* a refusal or ``unavailable`` (the app's access turned off or its level revoked,
  Angelo quitting, the headless child ending) answers that Angelo had the call,
  why its answer can't be read, that whether it finished is unknown and that
  nothing ran again; never a bare refusal that reads as "nothing ran";
* a connection that ends after the call was sent and before any answer says
  whether it ran is unknown, never "no window" (an app told that would repeat
  it elsewhere);
* without the window, a change of authority mid-call applies from the next call,
  never by restarting the child under the running call ("Headless").

(Fresh break-it review FR-H1, 2026-10-23: a long approved script came back "not
admitted" while it ran, and the app's retry ran it again.)

**Scripts.** In an External conversation "Trust scripts for this chat" cannot
be turned on and Always allow scripts does not apply (``trusted_scripts``).
Every script an app sends as code, and every script Angelo's agent writes for an
app's task, gets the person's exact-code review. The one standing consent an app
can get is narrower: a script file the person chose to always allow while it is
unchanged (`Script files`_).

Script files
------------

Maintainer decision (2026-10-23): an opt-in standing approval for an external
app's script file, keyed by the app, the project, the file's path and its
content hash; any change asks again. A person iterating a long build script
from Claude Code or Codex otherwise had to be at the screen for every run.

* ``angelo_run_script_file {"file": <absolute path>, "description"?}`` is
  offered to an app with the python level (``external_mcp_tools``). It is
  never one of Angelo's own agent tools.
* Angelo reads the file itself (``script_files.read_script``): the real path,
  opened without following a final link (one swapped in after resolving fails),
  a regular file, UTF-8, at most 32,768 characters (``angelo_execute_python``'s
  limit), read once. It then runs exactly that text as an
  ``angelo_execute_python`` call through the same host code (``ExternalWork``),
  and the call carries what was read (``ToolCall.script_file``, host-only). A
  file edited or swapped after the read never changes what runs. It runs as
  that file, as Blender's Text editor or ``blender --python`` would: ``__name__``
  is ``"__main__"`` and ``__file__`` its real path, so a main guard runs
  (break-it review FR-M1, 2026-10-23: such a file did nothing and reported
  success).
* The first run asks, as any script does. Once the person has opened its exact
  text, the card offers **Always allow this file while it's unchanged**.
  ``script_files.allow`` saves ``(app, project, path, sha256)`` in the state
  folder (``external-script-files.json``, owner-only, written atomically before
  it applies). The project is the open .blend's real path, so an unsaved file is
  never covered. The pump then runs the script under consent ``script_file``.
* A later run of the same file runs unasked only while every key still matches
  at admission and again at dispatch (``TrustedScripts.script_file_allowed``):
  the same app, project, real path and SHA-256 of the text that will run,
  approval Approve for me, ``AllowAutoApproval`` on, and a script that names
  neither preferences nor Angelo's add-on (``consent_excluded``). Anything else
  asks again. When the file changed, the card says since when.
* The same text sent as ``angelo_execute_python`` code always asks: only
  Angelo's own read of the named file carries the approval.
* The card's button never works while an agent script runs, so a script cannot
  approve itself.
* The app's row under External AI apps counts its allowed files and has
  **Forget script files**; Disconnect forgets them too.
* **Headless** (`Headless: a .blend without Angelo's window`_): the launcher
  gives the child the person's state folder when the app has the python level
  (``--approvals``); the child reads the approvals there and can never change
  them (``script_files.attach(read_only=True)``). Headless runs use the person's
  Angelo preferences (`Preferences`_), and the card's Always allow note says so.
  The project is the .blend the
  session opened, never the output its edits are saved to
  (``headless_host.bind_script_files``). An approved file runs and its result is
  saved to the session's output; anything that would need the card ends at once
  as ``needs_gui`` naming the file and project and how to approve it in the
  window. Raw ``angelo_execute_python`` stays refused headless before it reaches
  Angelo; inside a headless request only ``angelo_run_script_file`` can reach it
  (``external_apps.work_tools``). The child registers the window's own apply
  operators (``headless_main.apply_operators``), and a script whose consent no
  longer covers it when ``angelo.approve`` would run (the window would open its
  review there) is refused first (``HeadlessHost.refuse_unauthorized``);
  ``test_native_headless_registrations.py`` fails if the approval path calls an
  operator the child does not register. The host owns its document: a run that
  opens or saves as another file has nothing saved and the session's own file
  is loaded again, and a failed script always reloads the last saved state, so
  a partial change is never saved by a later edit; whether a call ran at all is
  read from its result's fields (``NEEDS_GUI``, ``scene_changed``), never its
  words. ``test_native_headless_script_files.py`` (in process) and
  ``test_blender_headless_script_files.py`` (a real ``Angelo --background``
  child: approve, run, change, refused, approve again, run; the team and machine
  policies) prove it.

``test_native_script_files.py`` covers the reader, the store, the catalog and
the whole path through the real ``ExternalHost`` and MCP ``Server``: the first
run and the button, an unchanged run, a changed file, another project, an
unsaved file, another app, Ask, policy, forgetting, preferences and add-on
scripts, the same code as ``angelo_execute_python``, a file changed before
dispatch, bad paths and levels.

**Alongside the person's chats.** An app works on the scene as another chat
does: dispatch is shared round-robin, native programs take the one writer
lease, revisions are checked at staging and commit, and each chat's change feed
reports the other's edits as hints. A snapshot is refused while another chat is
staging; the app's call then reports busy and is retried.

Task level
==========

``angelo_task`` (``task``, ``wait_seconds`` up to 50) starts one turn of
Angelo's agent in the app's External conversation through
``Controller.send_external_task``: the same path as a typed message
(``ui.prepare_controller`` binds the conversation's connection; the user row is
``request_kind: external_task``). The conversation's own model picker chooses
the connection and model; an unbound conversation adopts the person's
preferred ready connection, as an unbound chat does, and with none the task
does not start and says to connect in Angelo. Angelo never signs in or adds a
connection for an app.

``angelo_task_status`` (``task_id``, ``wait_seconds``) returns ``state``
(``running``, ``waiting_for_approval_in_angelo``, ``done``, ``failed``,
``stopped``), the latest progress rows (tool activity, assistant and system
text), the agent's reply once it ends, the request's snapshot id and the
connection kind that ran it (``runtime``, ``billing``: subscription, API key or
team; ``model``), never an account identifier. ``angelo_task_stop`` is the
person's Stop. One task runs at a time per app; tool calls wait while it runs.

Billing
=======

At tool level Angelo runs no model: the app's own subscription or key pays for
its model. At task level the External conversation's connection pays, as for
any chat; team connections go through the organization's gateway and policy.

Organization policy
===================

``AllowLocalCLIAccess`` false closes the socket for CLI clients and apps alike.
``RequireAskForEdits`` and ``AllowAutoApproval`` apply to External
conversations as to chats.

Both sources apply without the window too: the machine policy, read by the
launcher and the child themselves, and the signed-in organization's app policy
as last received by the window. The window records the organization policy it
applies in ``<state>/mcp/organization.json`` beside the app's key (owner-only,
atomic, from ``pump`` whenever it differs, retried until written,
``ExternalHost.record_organization`` in ``external_records``), and records "no
organization" explicitly (``{"organization": null}``) while signed out or when
no team sign-in is available, so signing out records none, also after a console
switch. A background Blender records nothing. The MCP launcher reads that record
at every headless call (``external_clients.read_organization``): a record that
is missing (never written, a write that failed, a different state folder,
deleted), unreadable, malformed, larger than 64 KiB, a link or not a regular
file counts as the strictest policy, like an access switch never recorded
counts as off; only an explicit "no organization" applies none, so a person
upgrading opens Angelo once, signed in or not; until then a headless call says
so ("Open Angelo once"), never that an organization turned access off (re-check
FR2-L4). Every reader of
these records opens only a regular file, without following a link at its name
or waiting on a FIFO, and reads at most 64 KiB (``team_org_policy.read_regular``).
The launcher refuses when either policy turns local access off, runs the child
under Ask when either requires asking for edits, and hands the child the
organization's policy (``--organization``), which the child installs as its
organization source, so ``AllowAutoApproval`` holds there too. A change in a
field that governs what the app may do (not the admin contact) restarts the
child before the next call, and the result says so. The launcher reads this
computer's policy (MDM, Group Policy, ``/etc/angelo``, ``ANGELO_POLICY_FILE``)
again at every headless call too (``managed_policy.reload``, about 0.02 ms), so
an admin's change reaches a running session from its next call; an unreadable
policy refuses.

``<connections>/team-org-policy.json`` remains the window's own copy for an
offline or restoring start; the launcher no longer reads it.

Outside the window the organization policy is only as strong as the person's
own files: a same-user process can rewrite the record, as it can the access
switch or Angelo's state. The machine policy (doc/angelo/enterprise_policy.rst:
MDM managed preferences on macOS, Group Policy on Windows, ``/etc/angelo`` on
Linux) is tamper-resistant against the person for what the launcher enforces,
which runs none of the person's code: access (``AllowLocalCLIAccess``),
``AllowAutoApproval`` (an ``angelo_run_script_file`` is refused there) and
``RequireAskForEdits`` (with the person's Ask or Read only choice: edits and
script files are refused there as ``needs_gui``), in the child's own words
(``Server.headless_policy_refusal``). The child applies them too, but code the
person installs runs inside the child (`Preferences`_), so the child's own
check alone could be patched (break-it review F3). (Break-it reviews, 2026-10-23: headless sessions used to see only
the machine policy, then read a missing file as no organization, then read the
machine policy once per launcher.)

Blender MCP
===========

Blender Lab's MCP add-on (extension ``mcp``) is not part of Angelo and is never
merged into Angelo's server. It runs its own TCP server, by default on
``localhost:9876``, that runs any Python a connected MCP client sends, outside
Angelo's approvals, snapshots, Undo steps and policy; its MCP client entry is
its own (usually ``blender``). Angelo's server opens no port, so the two never
conflict, and an app may have both entries.

Angelo does not change, enable or manage the add-on.

Headless: a .blend without Angelo's window
==========================================

**Status: ships in Angelo 1.0.5** (maintainer decisions: 2026-10-20, option A,
reads, plus option C, bounded edits saved to a new file; 2026-10-23, in 1.0.5).
Headless Python (maintainer decision 2026-10-23): an app's script file the
person always allows while it is unchanged runs headless under that same
approval, and any other script is refused with how to approve it.

An app can work on a ``.blend`` file while Angelo's window is closed: scene
reads, views and Angelo's bounded typed edits, with every edit saved to a **new
file next to the source**. The source file is never written. Nothing waits for a
person, so nothing that needs one runs: Python, background jobs, tasks and any
call that would show an approval card answer ``needs_gui`` at once.

Shape
-----

::

   MCP client --stdio--> angelo_mcp.py (one per client session)
     no file opened:  the local CLI socket, as above (Angelo's window)
     angelo_open_file: a child process the launcher starts and owns:
        <Angelo> --background --disable-autoexec --offline-mode
                 --python <scripts>/angelo/angelo_headless.py -- --in FD --out FD
                 --preferences yours --source-identity [...] ...
          (BLENDER_USER_CONFIG = a private copy of the person's preferences;
           with factory settings instead: --factory-startup, no copy)
        two inherited anonymous pipes (frames as the CLI's; no socket, path or port)
        --> headless_host.HeadlessHost (an ExternalHost) --> RequestService (allow-list, bounds, rate)
          --> main-thread loop (no timers in background mode): drain, pump_apps,
              pump_controller_tools(BlenderExecutor(window), ...) --> ExternalWork --> DirectRequest
              --> Controller.request_tool, Approve for me

The child is Angelo itself in background mode, so a call runs the same host code
as in the window: ``RequestService`` admission, ``ExternalWork``,
``DirectRequest``'s validation, presentation and short identifiers, and
``Controller``'s Auto lane for bounded typed edits. Background Blender keeps the
factory start-up file's window record (no OS window; the person's
``startup.blend`` is never loaded); it is the executor's window, so its 3D
Viewport serves views (``gpu.init()`` makes the GPU available) and sculpt
tools. The document is loaded into it with
``wm.open_mainfile(load_ui=False, use_scripts=False)``: the file's data, never
its UI or scripts, and from a **working copy** next to the source, never the
source itself (`The working copy`_).

**The document as in the window: Undo history and saved state.** Background
Blender skips a window's bookkeeping. It keeps no Undo history (every load frees
it and only a window's load starts it again), and a native commit
(``angelo_scene_program`` and ``angelo_apply_recipe`` with ``APPLY``) needs the
document's active Undo step, so it refused in the child ("Enable Global Undo
with at least one Undo step"). And only a window's event loop marks a loaded or
saved file as saved (the ``ND_FILEREAD``/``ND_FILESAVE`` notifiers), so after
the first typed edit ``angelo_blend_file`` reported ``unsaved_changes: true``
for good, even right after "Saved as". The start-up shared with parallel-agent
workers (``headless_main._prepare_preferences``) therefore asks the binary,
before its first load, to keep the document as a window does
(``_angelo_undo.keep_background_document``, ``wm_files.cc``): from then on
every load (the working copy, ``--resume``, each reload) starts the loaded
document's own history, its first step "Original", and marks it saved, as a
save of the open file does; the child's own saves are copies it places
(`The working copy`_), so ``HeadlessHost.save`` marks the document saved once
the output is in place (``_angelo_undo.background_document_saved``); so
``unsaved_changes`` means edits not yet in this session's output (the source
never holds them, though ``angelo_blend_file`` names it). No step of
an earlier file survives a reload, and
each committed edit is one Undo step, as in the window. The start-up also
forces Global Undo on and the step count to ``UNDO_STEPS`` (8): a person's 0
would refuse every commit with no window to change it, and nothing in the child
undoes past a reload, so more steps would only hold memory (the preferences are
protected, never saved). Pinned by ``test_native_headless_undo_history.py``,
``test_blender_background_undo_history.py`` (each load's history is that file's
alone; loads and saves read as saved) and, in a real child,
``test_blender_headless_native_commits.py``.

Choosing the target
-------------------

Three launcher tools exist only on this path (like the task tools, they are not
Angelo tools, so ``TOOLSET_VERSION`` is unchanged):

``angelo_open_file {"file": "<absolute path to a .blend>"}``
   Starts (or reuses) the session's child on that file and answers with the
   file's summary (``angelo_blend_file`` and ``angelo_inspect_scene``), the
   levels it allows here and where edits will be saved. From then on every
   Angelo tool call of this session goes to it.
``angelo_file_status {}``
   The open file, its output file if any, whether either changed on disk.
``angelo_close_file {}``
   Stops the child; later calls go to Angelo's window again.

With no file opened and no Angelo window reachable, a call answers
``no_file``: "Angelo isn't open. Call angelo_open_file with a .blend's absolute
path to work on it without the window." If Angelo's window is reachable and has
the same file open (``status`` gains ``file``, a digest of the open file's real
path; the launcher compares its own digest), ``angelo_open_file`` says so and
the session keeps using the window: the live document, unsaved changes
included, under the person's approvals. A window that does not listen (access
off) cannot be asked; the child then reads the saved file on disk.

Lifecycle
---------

* **Start:** at ``angelo_open_file`` (or the launcher's ``--file PATH``, for
  scripted use). The child answers ``ready`` once the file is loaded; cold
  start is measured below.
* **One instance per file per session.** A session has at most one child;
  opening the same file again reuses it, opening another stops it first.
  Another app's session starts its own child: there is no shared listener to
  connect to, and output names never collide (below).
* **Idle:** the child exits after ``IDLE_EXIT`` (10 minutes) without a call.
  The next call restarts it on the session's latest saved state (its output
  file once an edit was saved and only while it is still the file the child
  wrote, else ``stale``; the source when nothing was saved) and the result says
  to observe again, because earlier identifiers are unknown to the new process.
* **Authority every call.** The launcher reads the app's levels, the
  person's approval choice for its External conversation in the window and the
  organization's policy before each call, inside the session's lock (so no call
  runs on a read made before another call's restart finished); a change restarts
  the child with it first, so a revoked level or a stricter mode holds from the
  next call, and the result says the child started again.
* **Never mid-call.** A change while a call runs applies from the next call:
  the launcher never restarts the child under that call or sends it again, and
  a later round of a call goes only to the child that admitted it (the session
  numbers its children), so a child started meanwhile by another call never
  sees it. A call counts as running from the moment it is sent. A change that
  only adds access (levels a superset, an approval mode no stricter, policy
  switches only opened: ``only_more_access``) waits until running calls finish
  (the next call answers ``busy``, retry); any other change, a mixed one
  included, stops the child at the next call (access turned off: at the running
  call's next round), and the running call's next round says it was stopped and
  why. With no other call, a running call finishes first, as a running script
  does in the window. A child that ends mid-call answers with the outcome unknown
  (observe the scene before repeating it). A script never runs twice. (Break-it
  re-checks FR2-L1/L2 and FR3-L1/L2, 2026-10-23.)
* **A start that fails names why:** the child's own output goes to an
  owner-only file in its private folder (removed with it); when it never says
  ready, the error and the launcher's log carry its last lines, with the private
  folder, the home folder and the user name replaced (a file a build did not
  ship names itself; release-105, 2026-10-23).
* **End:** the child exits when its input pipe closes, so a launcher that exits
  or dies never leaves it behind. A child that dies answers ``unavailable``
  and the next call restarts it.
* **Changed on disk:** the source's device, inode, size and modification time
  are the launcher's, read before the child started (``--source-identity``),
  never the child's after its open: a change during the open stops the session
  as ``stale``; every call compares the source (and, after an edit, the output)
  before it runs, and every result says when the source changed during the call
  ("changed on disk during this call: an approved script or one of your add-ons
  wrote it"), never "unchanged". A difference answers ``stale`` at the next
  call; ``angelo_open_file`` again reloads. Known limit: the comparison is of
  stat fields, so an in-place write of the same size that restores the
  modification time to the nanosecond is not seen (the change time is left out
  on purpose: a metadata-only change such as a Finder tag or a quarantine
  attribute would make every session falsely stale).
* **Nothing left behind:** a session that saved no edit removes its working
  copy (and a ``.blend1`` beside it) when it ends, while it is still the file
  the session had; after a crash the launcher does (``headless_working_copy``).
  The launcher makes the copy a child starts on, so a start that fails in any
  way (no ready, a crash, a hang, a kill) removes it again while unchanged.
  A crash at worst leaves a copy of the source, never a change to it.
* **Alongside the window:** the child and Angelo's window are separate
  processes with separate data. Reads see the file as saved; edits never touch
  the file the window has open.

Preferences
-----------

Maintainer decision FR-M2 (2026-10-23): a headless child loads the person's
Angelo preferences, **read-only**, so it matches Angelo's window: the add-ons,
extensions and script folders they turned on, and their render devices (the
Cycles device). ``headless_preferences`` owns it.

* **Where from:** the config folder Angelo's window uses by default, found from
  the account's home folder (``CFFIXED_USER_HOME`` on macOS, as Blender's own
  lookup honours it, else the account's home; ``HOME`` on Linux). A
  ``BLENDER_USER_*`` variable in the connected app's environment is never
  honoured: the app never chooses what the child loads, as ``KEEP_ENVIRONMENT``
  already drops those variables.
* **Read-only by construction:** the launcher opens the person's
  ``userpref.blend`` for reading only and copies it (a regular file, at most
  64 MiB) into the child's private folder; the child starts without
  ``--factory-startup`` and with ``BLENDER_USER_CONFIG`` at that copy, so Blender
  loads the preferences itself and every save of preferences in the child
  (Blender, an add-on, an approved script) writes the copy, which is removed with
  the folder. The child also turns preference saving off in memory first
  (``use_preferences_save``, ``bpy.app.use_userpref_skip_save_on_exit``), and a
  background Blender never saves preferences on exit anyway. Only
  ``userpref.blend`` is copied: an add-on that keeps its own files in the config
  folder sees an empty one.
* **Factory settings, said so:** no file, a file that cannot be read, a file
  Blender cannot read (the child checks the copy with Blender's own reader and
  says ``preferences_unreadable``), or a child that never says ready with the
  person's preferences, or an error that is not the file's (an add-on that
  crashes, hangs or breaks the start): the launcher starts the child again with
  ``--factory-startup``, as before. A start with the person's preferences has
  its own budget (break-it F4: a hanging add-on cost the 120 s ready timeout
  every time): Blender must reach Angelo's start-up (the child writes its
  ``child.pid``) within ``STARTUP_BUDGET`` (20 s) and say ready within
  ``READY_BUDGET`` (35 s); with a factory start that fits a 60 s MCP tool
  timeout for an ordinary file. A file that takes longer to open with the
  person's add-ons opens with factory settings and says so. A failed start is
  remembered for the launcher's life, keyed by the preferences file's identity,
  so later starts go straight to factory settings with the same words until that
  file changes (never on disk: one slow file must not cost a person their
  preferences in later sessions).
* **Said in results:** ``angelo_file_status`` carries ``preferences`` (and
  ``working_on_source`` in a read-only folder), and the child adds the same
  words to every ``angelo_blend_file`` result, which the open result's summary
  shows, after naming the source as the open file and the working copy as a
  copy: ``Preferences: yours (N add-ons on, Blender's defaults included)``, with
  ``; did not load: <names>`` for enabled add-ons that failed (an exception in
  ``register()``, a missing module: named, never fatal), or ``Preferences:
  factory settings (<why>)``. The script-file card in the window says that headless runs use the
  person's preferences.
* **Angelo's own add-on** is listed in real preferences; in the child its
  ``register()`` does nothing (``ANGELO_HEADLESS=1`` in a background process), so
  the chat, sign-in, migrations, timers and a read-write script-file attach never
  run there: ``headless_main`` registers only what an app's calls use.
* **Unchanged:** ``--disable-autoexec`` and ``use_scripts=False`` (no text
  blocks or Python drivers from the file), ``--offline-mode``, the tools offered
  headless, approvals, the script-file rules and every team and machine policy.

**What the person's code can do.** With their preferences, the person's enabled
add-ons and start-up scripts run in the headless session as in the window. They
can write files and use the network: ``--offline-mode`` is advisory for add-ons,
which may ignore it. Angelo's guarantees cover Angelo's own gate (what an app's
calls may do, and the files Angelo writes), not code the person installed. The
working copy keeps that code from writing the source by saving the open file.

The working copy
----------------

Any of the person's add-ons can save the open file at any moment: a
``load_post``, ``save_post`` or ``depsgraph_update_post`` handler, an undo or
frame handler, a timer or a thread (break-it review, 2026-10-23). So Blender
never has the source open: at open the launcher claims the session's output
name next to the source (below) and fills it with the source's bytes through
``headless_files.place`` (``headless_working_copy.claim``), and the child opens
that copy (``--working``, accepted only as a .blend next to the source named as
its outputs are); a reload makes a fresh copy in the child. Every plain save by anyone lands in the
copy, and ``//`` image and library paths stay valid because it sits in the same
folder. The copy becomes the output with the first saved edit. A save of the
copy inside the child (one of the person's add-ons, an approved script) is the
session's own state, recorded by Angelo's own ``save_post`` handler, never a
change on disk by someone else. ``save_version`` stays the person's value and is
0 only inside the host's own save. Where the folder cannot be written, Blender
opens the source itself (a plain save cannot replace it there: Blender writes a
temporary file in the folder and renames it), the open result says the session
works on the file itself, and a write to the file is still detected as above.

Edits and the output file
-------------------------

* An app with the edit level may use the bounded typed edits of the Auto lane
  (``EDIT_TOOLS`` without ``WINDOW_ONLY_EDITS``: ``angelo_job_start`` and the web
  export and preview). The child's External
  conversation is Approve for me unless the person set the app's conversation
  in the window to Ask or Read only, which holds here too; ``RequireAskForEdits``
  or ``AllowAutoApproval`` false also make every edit ``needs_gui``. A call that would
  show an approval card in the window (anything outside the Auto lane) is
  finished at once as ``needs_gui``; nothing ever waits for a person here.
* After each call that committed an edit, and once no program is staging (a
  preview is accepted or discarded first), the child saves. The output is the
  working copy, claimed at open as ``<stem>.angelo-<YYYYMMDD-HHMMSS>.blend`` in
  the source's folder (``-2``, ``-3``... on a collision) with
  ``O_CREAT|O_EXCL``; every save checks it is still the file the child wrote
  (else a new name is claimed).
* **Blender never writes in the shared folder.** A save writes ``<target>@``
  and renames it over the target, following a link someone placed at that
  predictable name, which in a folder others can write could rewrite the
  source (found by the break-it review, which also showed that a private
  folder made *inside* the shared folder can itself be swapped for a link). So
  Blender saves only into a private folder in this user's own temporary folder
  (``save_as_mainfile(copy=True, relative_remap=False)``: paths saved as they
  are, which stay right because the output is in the source's folder), and
  ``headless_files.place`` copies the bytes next to the source through a new
  hidden file it creates with ``O_CREAT|O_EXCL|O_NOFOLLOW`` and writes by
  descriptor, then renames it onto the claimed name. A rename replaces a
  directory entry and never writes through a link, so whatever is placed at
  either name, no other file is written. The session's own file path is the
  working copy's; the host itself never calls a plain save (one of the person's
  add-ons may, and it writes the copy).
* Every edit's result gains one text block: ``Saved as <path> (a new file;
  <source name> is unchanged).`` The person opens it in Angelo to review.
* An edit that ended the request (``DirectRequest`` closes it after a failed
  edit that may have changed the scene) reloads the last saved state, so no
  partial change is ever saved; the result says to observe again.
* A folder that cannot be written answers ``output_unwritable`` before the edit
  runs (checked at open and at the first edit).

Tools offered headless
----------------------

========= ==================================================================
Level     Headless tools
========= ==================================================================
read (A)  ``READ_TOOLS`` without ``angelo_job_cancel``: inspection, search,
          objects, relationships, delta, verify, settings, ``read_data``,
          ``blend_file`` (``file`` relative to the source's folder), views
          (``angelo_view``, sculpt views; image content), previews, recipes,
          API lookups, capabilities, the empty brief
edit (C)  ``EDIT_TOOLS`` without ``WINDOW_ONLY_EDITS``; saved to a new file
python    ``angelo_run_script_file`` for a script file the person always
          allows while it is unchanged (`Script files`_); its result is saved to
          the new file as an edit's is; any other file ends as ``needs_gui``
          with how to approve it
never     ``angelo_execute_python`` code from the app, background jobs
          (renders, bakes and exports write files), the web export (a file)
          and live preview (a server and a browser), tasks (no provider runs
          headless), and everything never offered to apps
========= ==================================================================

Codes (``structuredContent.code`` and the text): ``needs_gui`` (the tool or an
approval needs Angelo's window), ``no_file``, ``invalid`` (not an absolute path
to an existing readable ``.blend``), ``stale``, ``output_unwritable``,
``busy``, ``unavailable`` (the child failed to start or died), ``refused`` (a
level the person did not allow), ``tool_failed`` and ``ok``, as the CLI's.

Safety
------

* **No listener.** The launcher and its child talk over two anonymous pipes the
  child inherits; nothing else can connect, and there is no socket file, port or
  token to steal. The app must be connected in Angelo (its key file exists and
  its levels apply); Disconnect turns headless off for it at the next call, and
  ``AllowLocalCLIAccess`` false or ``ANGELO_DISABLE_LOCAL_CLI=1`` turns it off
  for every app. The window's "Allow apps on this computer to reach Angelo"
  switch governs headless sessions too ("Turning this off closes the
  connection"): the window records it in ``<state>/mcp/access.json``
  (owner-only, whenever it differs, ``ExternalHost.record_access``), and the
  launcher refuses every headless call unless that record says on; a switch
  never recorded counts as off, so a person upgrading opens Angelo once.
  (Break-it review, 2026-10-23: it used to govern only the window's socket.)
* **No code from the file.** ``--disable-autoexec`` and ``use_scripts=False``
  run no text blocks or Python drivers, ``--offline-mode`` keeps Blender's online
  access off, and the child starts no provider, catalog or sign-in. The person's
  own add-ons and start-up scripts do run, as in the window
  (`Preferences`_).
* **State.** The child's Angelo state is a private ``0700`` temporary folder
  the launcher makes and the child removes when it exits (the launcher also
  closes its session when the app ends, and removes folders of children that
  died without cleaning up): it never adds conversations to the person's chats
  or reads their connections. The launcher appends one line per call to
  ``<state>/mcp/headless-audit.jsonl`` (time, app, tool, argument digest, code,
  output file name; never arguments or results).
* **Files.** Angelo creates only the working copy, which is the output
  ``.blend`` once an edit is saved and is removed otherwise. The source is only
  read, by the copy and never by Blender. The person's add-ons can write other
  files, as in the window.
* Bounds and rate are ``RequestService``'s (1 MiB requests, 8 MiB responses,
  20 calls a second, 16 queued); results are projected as on the window path,
  except that the source's own folder is shown as the app gave it.
* **Platforms:** macOS first. The inherited-pipe transport does not need the
  Windows named pipe, so Windows can follow after its own native validation.

Parity with Blender MCP's CLI tools
-----------------------------------

Blender Lab's MCP server runs ``blender --background <file> --python-expr``
once per call (``execute_blender_code_for_cli`` and the
``get_blendfile_summary_*_for_cli`` tools), and syncs a numbered copy when a
running Blender has the file open with unsaved changes. Angelo keeps one child
per file, so only the first call pays Blender's start-up; its reads, views and
edits are Angelo's typed tools; its file summary (``angelo_blend_file``) covers
datablocks, unused data, linked libraries and missing files; and a file open in
Angelo's window is used live. **Headless Python** is narrower than Blender
MCP's by design: only a script file the person always allows while it is
unchanged runs without the window (app, the opened .blend's real path, the
script's real path, its SHA-256); code sent inline, and an unapproved or changed
file, are refused with how to approve it.

Headless latency
----------------

``bench_headless_mcp_latency.py`` on Mac 1 (2026-10-20, holding the billed lock, no model, no window, both sides
with the same private HOME): this branch's Python at 71915080e2c on the 1.0.2 release binary against stock Blender
5.2.0 with Blender Lab's MCP server from the frozen benchmark runtime. p50 / p75 ms, 20 warm calls after 2 untimed
rounds (alternating order) and 5 cold opens per side; 0 errors on either side.

============ =========================== =================== =========================== ===================
Operation    Factory scene: Angelo       Blender MCP CLI     500 objects: Angelo         Blender MCP CLI
============ =========================== =================== =========================== ===================
open (cold)  896 / 909                   477 / 478           928 / 933                   494 / 495
summary      20.5 / 21.0                 484 / 488           23.4 / 24.4                 491 / 500
objects      20.1 / 20.5                 480 / 498           24.9 / 25.7                 492 / 494
add cube     49.6 / 51.2 (saved)         508 / 519 (saved)   75.7 / 76.7 (saved)         523 / 532 (saved)
move         45.8 / 47.3 (saved)         507 / 523 (saved)   65.5 / 66.4 (saved)         517 / 527 (saved)
picture      20.3 / 21.4 (view)          605 / 615 (render)  32.6 / 33.9 (view)          623 / 629 (render)
============ =========================== =================== =========================== ===================

Every Blender MCP CLI call starts Blender, so each costs a cold start; Angelo pays one at ``angelo_open_file``
(about 0.4 s more than one CLI call: the child also loads Angelo's modules, readies the GPU and returns the
file's summary and objects) and is ahead from the second call on. Edits on both sides include saving the file.
The pictures differ in kind (an offscreen viewport view against a Workbench render to a file). One machine, one
run; not for publication as is. These numbers predate the 1.0.5 base (1.0.2
binary); 1.0.5 re-measures only if a Mac 1 release slot has room, and otherwise
says the latency is untimed on 1.0.5.

Planned: what each client repeats
---------------------------------

**Status: plan, not built; a measurement, not a condition for headless sessions
(coordinator, 2026-10-23).** Codex has no channel for a server's instructions
and copies them into every Angelo tool's description it shows. Measured on the
headless branch (Codex 0.163.0, stand-in model, 2026-10-21):

======================= ===================================================== ==============
Client                  What its model receives of Angelo's text              Size
======================= ===================================================== ==============
Claude Code             instructions once + ``tools/list``                    4.9 + 84 KB
Codex, up front         one namespace description + 42 tools                  76 KB
(gpt-4.1)
Codex, code mode        nothing at first; each Angelo entry in ``ALL_TOOLS``  5.1 to 10.5 KB
(GPT-6)                 is the whole instructions + its description           per tool,
                                                                              230 KB for 42
Codex, tool search      the instructions with each tool it loads              as code mode
(GPT-5.5)
======================= ===================================================== ==============

Plan:

* The server tells clients apart by ``initialize.clientInfo.name`` (a
  per-client presentation, never per scene). Claude Code's channel is
  unchanged: the full instructions once.
* For every other client the instructions shrink to a short header (who
  Angelo is, who approves, that results carry the host's read-back, about
  600 bytes), and each fact moves to the one tool whose arguments need it:
  ``agent_guidance.BLENDER_API_FACTS`` into ``angelo_execute_python`` and the
  program tools that take raw RNA paths and values (``angelo_scene_program``,
  ``angelo_apply_recipe``), the identifier and revision facts into the tools
  that take handles. Each stays one shared constant, assembled per tool.
* Measure per client: bytes and cached-token cost of the first request, of
  reading one tool, and of an unfiltered ``ALL_TOOLS`` dump; the same
  discovery prompts as the 1.0.3 check, on Sol, Astra and Opus, before and
  after, with the quality bar unchanged (every task's checks).
* The prompt audit checks facts per route: ``mcp-claude-code`` in the
  instructions, ``mcp-codex`` in the descriptions of the tools that carry them
  (recorded from a probe turn that reads ``ALL_TOOLS``).

Validation
==========

* Host (no Blender; Linux CI with ``--max-skips 0``):
  ``test_native_external_mcp.py`` (registry and keys, levels, the handshake's
  app proof and revocation, the in-process launcher through the real listener,
  reads, Approve-for-me edits, every script reviewed, declines, idle close,
  Stop, file change, tasks with a stand-in provider: state, approval wait,
  reply, stop; the status-bar notice; no network socket in the launcher's code) and
  ``test_native_external_setup.py`` (every app's settings shape, other entries
  kept, backups, unsafe files left alone, Codex round trip).
* Native: the CLI fixture (``external_cli.rst``) plus real clients started on
  a test profile; per-call latency against Blender + MCP is measured by
  ``tests/python/angelo_agent/bench_external_mcp_latency.py``.
* Headless, host: ``test_native_headless_mcp.py`` (file rules and output
  claims, the allow-list, the child's flags and environment, real pipes with a
  real child process, and the launcher through real pipes to a real
  ``HeadlessHost`` and ``Controller``: ``no_file``, reads, edits saved to one new
  file and never the source, ``needs_gui`` without waiting, previews, stale
  files, a changed output never saved over, reload after a failed edit,
  Disconnect and policy, read-only apps, restart on the saved output, close).
  Native, on a built app (no window): ``test_blender_headless_mcp.py`` (the
  real launcher and background child on a saved scene: the source's bytes, one
  output that reopens with the edits, no ``.blend1``, a view image, no network
  listener, the child and its private state gone after close) and
  ``test_blender_headless_native_commits.py`` (an APPLY program and recipe each
  commit one Undo step on the first load, after a reload and after a restart on
  the output). Latency against
  Blender MCP's CLI tools: ``bench_headless_mcp_latency.py``.
