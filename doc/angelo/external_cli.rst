.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

External coding-agent CLI (``angelo``)
**************************************

**Status: phase 1 (read-only) implemented; later phases proposed.** The
"Phase 1 contract" sections describe current behavior. "Later phases
(proposed)" describes design that is not implemented. Phase 1 adds no tool,
schema or tool-result field, so it does not change ``TOOLSET_VERSION``.

Purpose and non-goals
=====================

A coding agent working in a game or application repository (Claude Code or
Codex in a terminal, an IDE agent, CI) can observe the open Angelo document
through the same typed tools Angelo's own chat uses, while the artist keeps
approval authority in Angelo's UI.

Not provided: a second execution engine, an MCP server or wrapper (project
requirement: no MCP execution), a screenshot/computer-use layer, any TCP or LAN
listener for the CLI, model inference, a new approval path or new tool
definitions. (Angelo's only network listener is the team sign-in callback on
127.0.0.1, which exists only while a sign-in waits for the browser;
``ai_providers.rst``, "Team account and team connections".) The CLI
is a local transport into existing owners: ``tool_specs`` (``TOOLS``,
``validate_call``, ``dynamic_tools``), ``Controller`` (``request_tool``,
``RequestFence``, document binding, ``pump_tools``, ``stop``), the
``invocations`` ledger, ``tool_finalization`` receipts and ``ui.pump`` with
its shared ``ToolDispatchBudget``.

Design rules
============

* **Transport:** ``CONTRIBUTING.md`` (Agent Readiness) allows exactly this
  off-by-default, local-only, authenticated transport into the existing
  controller and executor owners, with no new tools, schemas, approvals or
  authority.
* **Conversations:** one ``External: <label>`` conversation per client label.
  It rotates only through an explicit ``angelo session new``.
* **No mutations yet:** grouped mutation requests, Auto bounded for External
  conversations, headless policy, extra tool eligibility and approval
  visibility belong to phase 2. Phase 1 implements no mutation request.
* **Codes:** machine ``code`` fields exist only in the CLI envelope, never in
  tool results, so ``TOOLSET_VERSION`` does not change.
* **Platforms and launcher:** macOS first. Linux shares the Unix-socket code
  (host tests only, no native Linux validation yet). Windows refuses to listen
  (named pipe not implemented). The launcher is
  ``scripts/angelo/angelo_cli.py`` run by the bundled Python in isolated mode
  (``-I``); it is not installed on ``PATH``.
* **Names and protocol:** the command, launcher, runtime directory, socket
  name, messages, tool names and HMAC contexts use ``angelo``. Earlier
  variable spellings remain deprecated fallbacks (``native_editor_build.rst``,
  "Legacy names"). The HMAC contexts are part of the wire contract and
  ``angelo-cli/2/...`` is CLI protocol 2: a protocol 1 client and a protocol 2
  app (or the reverse) answer ``incompatible`` (exit 9) instead of failing
  authentication. Use the launcher bundled with the running app.

Phase 1 contract
================

Architecture
------------

::

   coding agent shell -> angelo_cli.py -> cli_client.py (stdlib only, no bpy)
     -> Unix socket in a private runtime directory: peer UID check,
        token challenge, one framed JSON request per connection
        -> external_cli.LocalCliServer connection thread (no bpy)
           -> external_service.RequestService: allowlist, bounds, rate limit
              -> bounded WorkQueue -> external_host.ExternalHost.drain in ui.pump
                 -> Controller.open_external_read + request_tool (main thread)
                    -> pump_tools -> BlenderExecutor (shared dispatch budget)

==================================== ==========================================
Module                               Responsibility
==================================== ==========================================
``external_cli_protocol.py``         Framing, token proofs, exit codes, envelope,
                                     endpoint/runtime-directory checks, escaping
``external_cli.py``                  Listener, peer credentials, endpoint
                                     record, connection limits, Windows refusal
``external_requests.py``             Read-only allowlist and exclusion reasons,
                                     ``tools`` catalog, result codes, path
                                     projection, work queue, rate limit, audit
``external_service.py``              Per-connection command handling
``external_host.py``                 Main thread: preference and kill switch,
                                     listener lifecycle, External conversations
                                     (``cli/clients.json``), requests, status
``cli_client.py``                    Client used by the launcher
==================================== ==========================================

Connection threads never touch ``bpy`` or a controller. ``request_tool`` is
called on the main thread (like ``accept_native_preview``), which avoids
journal saves racing main-thread transcript updates. A defect in the CLI host
stops the listener and is shown in Preferences; it never breaks ``ui.pump``.

Using the CLI
-------------

1. Preferences > AI Providers > **Allow local CLI access** (off by default;
   stored with the add-on preferences). The status line below the toggle
   reports listening, off, the kill switch or why listening failed.
2. Run the client with Angelo's bundled Python::

     PY=/Applications/Angelo.app/Contents/Resources/5.2/python/bin/python3.13
     angelo() { "$PY" -I /path/to/angelo-studio/scripts/angelo/angelo_cli.py --client claude-code "$@"; }

     angelo status
     angelo tools --tool angelo_scene_search --schema
     angelo call angelo_scene_search '{"query": "Cube", "coverage": "PAGE", "limit": 20}'
     angelo call angelo_scene_object '{"object": "scn:<handle from the search>"}'
     angelo call angelo_scene_delta '{"observation_token": "<token from a SCENE search>"}'

   Read ``exit`` (or ``$?``) and ``retry`` first; ``result`` is the tool's
   own result. Agents should pass ``--expect-document`` with the ``document``
   from an earlier envelope so a call refuses if another file was opened.
3. After ``stale`` (exit 5) because a different document was loaded, run
   ``angelo session new`` once, then observe again. Handles from the old
   document never work in the new one.

The client finds ``<state>/cli/endpoint.json`` through ``--state-dir``, then
``ANGELO_STATE_DIR``, ``BLENDER_USER_CONFIG``/``angelo-agent``,
``BLENDER_USER_RESOURCES``/``config/angelo-agent``, then Angelo's default
user-config directory (``<Angelo user folder>/5.2/config/angelo-agent``; see
``app_identity.rst``). The label is ``--client``, else
``ANGELO_CLIENT``, else ``external`` (1–32 of ``[A-Za-z0-9._-]``). Every
variable also accepts its deprecated spellings (``product_env``).

Transport and authentication
----------------------------

* **Off by default.** The listener runs only while the toggle is on, the
  ``angelo_agent`` add-on is enabled (the default layout enables it) and
  ``ANGELO_DISABLE_LOCAL_CLI=1`` is not set. Turning the toggle off, setting
  the kill switch or disabling the add-on withdraws the endpoint record and
  socket, fails queued work as ``unavailable`` and cancels admitted External
  reads. Quitting with access on removes both files through an ``atexit``
  hook that touches files only.
* **Runtime directory:** ``ANGELO_CLI_RUNTIME_DIR`` (absolute), else
  ``$XDG_RUNTIME_DIR/angelo``, else per user ``$TMPDIR/angelo-<uid>`` on macOS
  and ``/tmp/angelo-<uid>`` elsewhere. Created ``0700``; ``lstat`` must show a
  real directory owned by the effective UID with no group/other bits, or
  listening is refused. The socket is ``angelo-cli-<digest of the state
  directory>.sock``, bound under umask ``077`` and set ``0600``; a path over
  103 bytes is refused with advice to shorten the runtime directory.
* **Endpoint record:** ``<state>/cli/endpoint.json`` (directory ``0700``,
  file ``0600``, ``O_EXCL|O_NOFOLLOW``) holds protocol, socket path, PID,
  instance nonce and a 32-byte per-launch token. A leftover record is removed
  only when its PID is dead (or is this process); a live other PID refuses to
  start. A leftover socket is unlinked only if it is this user's socket and
  either its record named a dead PID or nothing accepts connections on it.
  Angelo's file tools cannot read the record: saved projects exclude the state
  directory and unsaved workspaces refuse ``..``; file tools are excluded from
  the CLI anyway.
* **Handshake:** the server sends a nonce challenge; the client answers with
  ``HMAC-SHA256(token, "angelo-cli/2/client", server nonce, client nonce)``;
  the server checks it with ``hmac.compare_digest`` and answers with the
  server-context HMAC, which the client verifies. The token never crosses the
  socket and never appears in argv, environment, output, logs or transcripts.
  The ``angelo-cli/2/...`` contexts embed the protocol number; changing them
  requires a new ``CLI_PROTOCOL``.
* **Peer credentials:** before any frame is read, the server reads the
  kernel's peer UID (macOS ``LOCAL_PEERCRED`` plus ``LOCAL_PEERPID``; Linux
  ``SO_PEERCRED``) and closes on a mismatch or on an API error. On platforms
  without such an API the private directory is the only boundary. PIDs are
  display and audit data only.
* **Framing and limits:** 4-byte big-endian length plus one UTF-8 JSON object;
  non-finite numbers are rejected. Requests up to 1 MiB, responses up to
  8 MiB; an oversized result is ``tool_failed`` with its call ID, never
  truncated. Each frame must arrive within 10 s (an absolute deadline, so a
  slow sender cannot hold a slot). At most 8 concurrent connections (others
  get ``busy``), 16 queued main-thread items, and 20 calls per second per
  label. One connection carries exactly one request.
* The transport opens no TCP, UDP or loopback listener (checked by a host
  test of ``external_cli.py`` and by ``lsof`` in the native fixture, which
  also checks that no team sign-in is waiting: the team sign-in callback is
  the app's only network listener and exists only during a sign-in).

Commands
--------

Every command writes one JSON envelope to stdout (``--format text`` for
people) and, when not ``ok``, a one-line note to stderr. JSON output uses
ASCII escapes; text output escapes control, ANSI, bidirectional and newline
characters in untrusted values.

``angelo status [--call ID]``
   Instance, toolset, document digest and the label's conversation: policy
   (``ASK``: an External conversation is created in Ask, never in the chat
   default, Auto bounded edits; see "Open questions for phase 2"),
   whether it is still bound to the open document, open request, pending
   calls and mutation-ledger use (n/4,096, closed). ``--call`` reports
   ``queued``/``executing``, ``finished`` with its receipt phase,
   ``admitted_result_pruned`` or ``unknown``. Creates no conversation and
   opens no request.

``angelo tools [--tool NAME] [--schema]``
   The 13 allowed tools with description, ``read_only``, ``bounded``,
   ``approval: none``, ``schema_sha256`` (of the compact ``dynamic_tools``
   schema) and, with ``--schema``, ``inputSchema``; plus every excluded tool
   with its reason. Discovery grants nothing; host validation stays
   authoritative. ``angelo_scene_verify``'s schema names its less common check
   kinds without their fields, as Codex's does; ``angelo call
   angelo_agent_capabilities '{"family":"CHECKS","names":[...]}'`` returns them.

``angelo call TOOL [JSON | -] [--args-file PATH] [options]``
   Runs one allowed read-only tool (arguments default to ``{}``). ``--id``
   (1–128 of ``[A-Za-z0-9._:-]``) is generated when omitted. Options:
   ``--conversation UUID`` and ``--expect-document SHA256`` (refuse with
   ``stale`` on mismatch), ``--toolset N`` (reads proceed with
   ``toolset_mismatch: true``), ``--wait S`` (default 90, max 300). SIGINT or
   SIGTERM detaches with a ``pending`` envelope carrying the call ID; host
   work is not cancelled (a killed shell command is not a human Stop).

``angelo session new``
   Creates a fresh ``External: <label>`` conversation, records the rotation in
   both transcripts and routes the label to it. Refused as ``busy`` while the
   current one has calls in flight.

``angelo watch``, ``stop``, ``observe``, ``view``
   Not available in phase 1: ``incompatible`` (exit 9) with guidance.

Envelope (all fields always present)::

   {"cli_protocol": 2, "ok": true, "code": "ok", "exit": 0, "retry": "never",
    "toolset_version": 17, "document": "<sha256>", "conversation": "<uuid>",
    "call": {"id": "<ID>", "tool": "angelo_scene_search", "state": "finished",
             "replayed": false, "admitted": true},
    "result": {...}, "message": "Completed",
    "timings": {"queue_ms": 48.9, "host_ms": 8.9, "total_ms": 58.8}}

``timings`` appears on completed calls: ``queue_ms`` until the main thread
claimed the request, ``host_ms`` from claim to result on the main thread (it
includes the tool and both journal saves), ``total_ms`` inside the service.
``document`` is ``invocations.document_digest`` of the host's document key.

==== ================ =========================================================
Exit Code             Meaning and the only safe next step
==== ================ =========================================================
0    ``ok``           Terminal, no error. Check outcomes stay in ``result``.
1    ``tool_failed``  The tool reported an error (including a stale handle or
                      revision inside the tool). Re-observe; do not repeat
                      unchanged.
2    ``invalid``      Unknown tool, schema, argument or JSON error. Nothing was
                      admitted.
3    ``not_applied``  Expired, or stopped in Angelo. No change.
4    ``refused``      Tool outside the phase-1 allowlist, or the call ID was
                      already used with different arguments.
5    ``stale``        Document or conversation changed (or the
                      ``--expect-document``/``--conversation`` guard failed).
                      ``session new`` after a load, then observe again.
6    ``pending``      Still running after ``--wait``. Repeat the identical
                      command (same ID replays the receipt) or poll ``status``.
7    ``uncertain``    ``serialization_failed`` or ``journal_durable: false``
                      (or, for later mutations, possible partial changes).
8    ``unavailable``  Not running, access off, authentication failed, or
                      access turned off while the call was waiting.
9    ``incompatible`` CLI protocol mismatch or a command not yet available.
10   ``busy``         Queue, rate limit or a non-responsive main thread. Not
                      admitted; retry later with the same ID.
==== ================ =========================================================

``retry`` is ``never``, ``poll`` (pending), ``after_observe`` (stale) or
``same_id_later`` (unavailable, busy). Codes are derived from host state and
result fields (``error``, ``expired``, ``journal_durable``, the controller's
document and cancellation state), never from message text. Handle- or
revision-level staleness inside a tool therefore surfaces as ``tool_failed``
with the tool's own message; tool results carry no machine codes.

Results are projected before leaving Angelo: ``path``, ``filepath``,
``file_path``, ``directory`` and ``root`` keys holding absolute paths become
``"<withheld>"`` (``path`` also gains an opaque ``view_id`` digest), and the
state, connections, project, temporary and home directories inside any string
become ``<angelo-state>``, ``<angelo-connections>``, ``<project>``, ``<temp>``
and ``<home>``. ``Controller.redact`` already removes credentials.

Conversations, requests and document binding
--------------------------------------------

* **External conversations.** ``cli/clients.json`` (``0600``, atomic replace)
  maps each label to one conversation. It is created by the first ``call``
  (or ``session new``) with a first user row ``External: <label>`` (``request_kind:
  "external_session"``), which titles it in the chat switcher, also in older
  builds, plus a system note. Its controller joins ``ui._controllers`` without
  switching the view; ``Controller.external_client`` marks it, also when the
  user opens it from the switcher first. The composer shows "External client:
  approvals and Stop only"; ``Controller.send``/``start_voice`` refuse there
  before any connection, snapshot or project brief is prepared, so a provider
  turn never competes for ``busy``. It never binds a saved-project brief. A
  label is routing data, not a security principal.
* **Read requests.** ``Controller.open_external_read`` advances the request
  generation and sets ``busy`` with no provider, snapshot or checkpoint
  (reads have no effects). Concurrent reads from the label share that fence;
  ``ExternalHost.close_finished`` calls ``close_external_read`` once no call
  is pending. Each read goes through ``request_tool`` with the key
  ``<conversation>/external/<ID>``, so it is journaled, appears as an activity
  row in the External transcript, reserves no mutation ID and replays its
  receipt for the same ID and arguments (``replayed: true``). The same ID with
  different arguments is ``refused``. Reads run in ``pump_tools`` under the
  shared one-native-execution-per-tick budget, round-robin with other chats.
* **Allowlist first.** ``RequestService`` refuses every tool outside the
  allowlist, and validates arguments with ``validate_call``, before any work
  is queued; nothing reaches a controller, journal or executor.
* **Document binding.** A load makes ``set_document`` invalidate the External
  conversation like any chat (pending reads end as ``stale``); later calls
  return ``stale`` until ``angelo session new``. Nothing rotates silently.
  After an Angelo restart the label's conversation binds to the document open
  at its first CLI use, as every chat does; ``--expect-document`` detects
  that the document differs.

Tool exposure
-------------

* **Allowed (read-only):** ``angelo_`` + ``inspect_scene``, ``inspect_sculpt``,
  ``scene_search``, ``scene_object``, ``scene_relationships``,
  ``scene_delta``, ``scene_verify``, ``inspect_preview``, ``expand_recipe``,
  ``blender_api``, ``agent_capabilities``, ``agent_metrics`` (this External
  conversation only) and ``memory_read`` (this conversation's empty brief).
  Import fails if any of them stops being annotated read-only.
* **Excluded, with the reason ``angelo tools`` prints:** reviewed Python;
  Studio (accounts and library); ``angelo_delegate`` (billing, no lead turn);
  file write/delete and ``angelo_memory_update`` (individually approved
  effects); file list/read (could escape the caller's sandbox);
  ``angelo_read_reference`` (nothing attached); ``angelo_set_workspace_mode``
  (artist's layout); ``sculpt_view``/``review``/``target`` and
  ``angelo_view`` (images; later ``view``); every scene mutation (phase 2).

Audit and measurement
---------------------

``cli/audit.jsonl`` (``0600``, trimmed to 256 KiB) records time, label, peer
UID/PID, command, tool, call ID, argument SHA-256 and code; never arguments or
results. Transcripts and the invocation journal remain authoritative. Phase 1
writes no telemetry receipts: the shared 256-receipt ring would let a polling
client evict the artist's request measurements. Latency is reported per call
in ``timings`` instead.

Native fixture measurements (macOS arm64 Release build, machine idle but not
otherwise controlled), medians of 10 ``inspect_scene`` and 6
``scene_search`` (``SCENE``, limit 50/10) calls from real client processes:

=========================== ============ =========== ============ ===============
Scene                       Tool         host_ms p50 queue_ms p50 client process
=========================== ============ =========== ============ ===============
Factory scene (3 objects)   inspect      8.3         51           121 ms
Factory scene (3 objects)   search       15.7        50           121 ms
2,003 objects, shared mesh  inspect      37.6        49           138 ms
2,003 objects, shared mesh  search       279.7       43           381 ms
=========================== ============ =========== ============ ===============

``queue_ms`` is the idle 0.1 s ``ui.pump`` interval. ``host_ms`` is one
contiguous main-thread section; for ``SCENE`` search at 2,000 objects it is
dominated by the tool itself (239–267 ms in ``performance.rst``), and the CLI
adds two transcript saves (about 7 ms on the factory scene, growing with the
External transcript). Polling clients should prefer ``coverage: "PAGE"`` and
``angelo_scene_delta``. Python start-up dominates the client process time.
The rate limit bounds call count, not main-thread time: a client issuing heavy
``SCENE`` searches continuously can still occupy the main thread, as a model
in any chat can.

Validation
----------

* Host (no Blender; run on Linux CI with ``--max-skips 0``):
  ``test_native_external_cli_transport.py`` (framing, proofs, private
  directories, endpoint safety, real sockets: token mismatch, impostor
  listener, peer mismatch, oversized and slow frames, connection limit,
  stale records, stop, exit hook, Windows refusal, no network listener),
  ``test_native_external_cli_service.py`` (allowlist, catalog, codes,
  projection, bounds, audit, client usage/escaping, file-tool boundary) and
  ``test_native_external_cli_host.py`` (real ``Controller`` and
  ``SessionCollection`` through the real listener and client: transcript,
  replay, refusals never reaching ``request_tool``, stale after a document
  change, ``session new``, shared fence, toggle-off, kill switch, failure
  latch).
* Native GUI fixture with private HOME, TMPDIR, user config, state, accounts
  and runtime directories (add ``--objects 2000`` for the scale cohort)::

    python3 tests/python/angelo_agent/test_blender_external_cli.py \
      --binary /absolute/path/to/Angelo --out <output directory>

  It enables the toggle, runs real client processes (status, tools, search,
  ten reads, refused mutation/Python/file read, invalid, ``observe``, text
  output, forged token), checks the External transcript and switcher title,
  loads another file (``stale``, ``session new``, guards), turns access off,
  tests the kill switch, quits while listening, and requires an unchanged
  scene, no provider start, no TCP listener and logs free of assertions,
  tracebacks and leaks. ``run_native_suites.py --gui`` runs it as a host
  driver; because that run directory is too deep for a 104-byte socket path,
  the fixture then uses a private ``/private/tmp/angelo-cli-run-<digest>``
  runtime directory and removes it afterwards.

Known gaps
----------

* Linux and Windows are not natively validated; Windows does not listen.
* Peer credentials are verified natively only on macOS; Linux
  ``SO_PEERCRED`` is exercised by host tests on CI.
* Whether agent sandboxes (Codex Seatbelt/Landlock, Claude Code) permit
  connecting to the socket is untested; allowlisting may be needed.
* Same-user processes can read the token; approvals (phase 2) remain the
  boundary. Phase 1 exposes no authority beyond reads.

Later phases (proposed)
=======================

Nothing in this part is implemented.

* **Phase 2:** bounded mutations (``BOUNDED_MUTATIONS``: programs, preview
  accept/discard, cube/transform/batch, sculpt) under Ask with required IDs;
  ``watch`` and ``stop``. A mutation request carries one call, opens on the
  main thread, freezes the checkpoint policy and, under ``EVERY_REQUEST``,
  captures the snapshot before admission (``capture_checkpoint`` refuses while
  any chat is staging, which surfaces as ``busy``). Mutation requests wait
  (FIFO, at most 8) for open reads to drain. The transcript records a
  user-role row with ``request_kind="external_call"``, the label, tool and
  checkpoint. Non-read-only calls reach ``pump_tools`` exactly as provider
  calls do: Ask raises the normal card and Read only refuses; the CLI cannot
  approve, decline, review or change policy. ``angelo_accept_preview`` from
  an External conversation always opens the human review. Host expiry stays
  authoritative (``ToolCall.expires``); SIGINT/SIGTERM detaches without
  cancelling. ``angelo stop`` and Angelo's Stop call ``Controller.stop()``;
  an executing call or commit is atomic and reports its actual read-back.
  After a client timeout the only correct actions are ``status --call ID`` or
  the identical command; an unfinished ``execution_started``/
  ``commit_started`` marker after a restart replays as ``uncertain`` and is
  never re-run. At 4,096 IDs new mutations are refused until ``session new``.
  With ``--toolset N``, mutations require an exact match (``incompatible``).
  ``watch`` would stream bounded NDJSON (default 256 events or 120 s: queued,
  approval required, declined or expired, staging progress, preview ready,
  terminal result, request closed; lost events as ``events_dropped``) from
  256-event/1 MiB rings per label, at most 4 streams. Audit gains telemetry
  receipts with an ``external`` runtime kind if the receipt ring is separated.
* **Phase 3:** ``observe`` (turn-context/change feed) and ``view`` (image
  bytes over the socket written by the client to ``--out``, so Angelo never
  writes into the caller's repository); ``sculpt_view``/``review``/``target``
  and ``angelo_view`` through ``view``.
* **Later:** Linux and Windows native validation; Windows named pipe
  ``\\.\pipe\angelo-<user-SID-digest>-<instance>`` with a current-user DACL,
  ``FILE_FLAG_FIRST_PIPE_INSTANCE`` and ``PIPE_REJECT_REMOTE_CLIENTS``,
  checking ``GetNamedPipeClientProcessId`` and the client SID.
* **Headless** (``Angelo --background``): current owners assume a window
  (``BlenderExecutor(window)``, ``document_key()``, ``capture_checkpoint``,
  ``ui.pump`` timers, Global Undo). Options: A read-only only (recommended
  first); B a GUI-confirmed, signed bounded policy file; C bounded mutations on
  a disposable copy; D relaying approvals to a running GUI. Rejected for every
  option: terminal ``y/N`` prompts, reviewed Python, Studio, delegation and
  file writes.

Open questions for phase 2
==========================

1. One mutation per request (one snapshot each) or grouped requests?
2. Auto bounded for External conversations; ever auto-accept a preview?
3. Headless policy: A, B, C, D or none.
4. May file tools, memory updates, Studio or reviewed Python ever be eligible?
5. Approval visibility: switcher prefix plus header notice, a toast, or
   auto-open.
6. A per-label main-thread time budget for External calls, given the measured
   cost of ``SCENE`` searches on large scenes.
7. A separate telemetry ring (or none) for External requests.
8. Launcher installation location and ``PATH`` exposure.

Security analysis
=================

* **Threat model.** Same user, local machine. Same-user processes can already
  read the profile and token and usually Angelo's memory, so the transport
  keeps out other users, remote hosts and accidental exposure and adds no
  authority. It does not defend against same-user malware.
* **Token theft** yields socket access only; phase 1 then allows reads only,
  and later mutations would still need approval in Angelo's UI.
* **Confused deputy.** Angelo acts with Blender's privileges, so an external
  agent could try to use the CLI to escape its own sandbox. Hence off by
  default, read-only allowlist, file tools and reviewed Python excluded, no
  approval or policy commands, labels that are not principals and
  display-only peer data.
* **Prompt injection.** Names, text and results are data, escaped in both
  output modes and never interpolated into instructions. They may still steer
  the external model.
* **Billing.** The CLI triggers no provider inference and exposes no paid tool.
