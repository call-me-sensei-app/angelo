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
=============================== ==================================================

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
python    ``angelo_execute_python``, always with exact-code review
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
  placeholders, as in the CLI.
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

**Scripts.** In an External conversation no standing consent covers a script:
"Trust scripts for this chat" cannot be turned on there and Always allow
scripts does not apply (``trusted_scripts``). Every script an app sends, and
every script Angelo's agent writes for an app's task, gets the person's
exact-code review.

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

Blender MCP
===========

Blender Lab's MCP add-on (extension ``mcp``) is not part of Angelo and is never
merged into Angelo's server. It runs its own TCP server, by default on
``localhost:9876``, that runs any Python a connected MCP client sends, outside
Angelo's approvals, snapshots, Undo steps and policy; its MCP client entry is
its own (usually ``blender``). Angelo's server opens no port, so the two never
conflict, and an app may have both entries.

Angelo does not change, enable or manage the add-on.

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
