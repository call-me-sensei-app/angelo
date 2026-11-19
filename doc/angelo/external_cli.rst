.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

External coding-agent CLI (``angelo``)
**************************************

**Status: 2.0.1.** The ``angelo`` command and its local socket are the transport external AI apps connected in
Preferences reach Angelo through (their MCP launcher, ``external_mcp.rst``). Since 2.0.1 no client runs a tool: an
external agent works only through Angelo's own agent, and ``call`` and the tool catalogue are refused for every
client (maintainer's decision, 2026-11-18; break-it review S5). A client label without an app key can read
Angelo's status and rotate its conversation, nothing more. Nothing here adds a tool, schema or tool-result field,
so ``TOOLSET_VERSION`` is unchanged.

Purpose and non-goals
=====================

A coding agent (Claude Code or Codex in a terminal, an IDE agent, CI) works with the open Angelo document by
connecting in Angelo and talking to Angelo's agent (``external_mcp.rst``), while the artist keeps approval
authority in Angelo's UI. This transport carries those commands.

Not provided: a second execution engine, any tool call from outside, an MCP server inside Angelo (MCP is spoken only
by the stdio launcher external apps start, ``external_mcp.rst``, and inside the direct-tools Claude session's own
stdio, ``agent_integration.rst``, "Direct tools (opt-in)"), a screenshot/computer-use layer, any TCP or LAN
listener, model inference or a new approval path. (Angelo's only network listener is the team sign-in callback on
127.0.0.1, which exists only while a sign-in waits for the browser; ``ai_providers.rst``, "Team account and team
connections".)

Design rules
============

* **Transport:** ``CONTRIBUTING.md`` (Agent Readiness) allows exactly this off-by-default, local-only,
  authenticated transport into the existing owners, with no new tools, schemas, approvals or authority.
* **No tools from outside:** ``call`` and the ``tools`` catalogue answer ``refused`` (exit 4) for every client,
  with how to connect an agent; the refusal is audited. A connected app's ``agent`` commands are the only work a
  client can hand Angelo (``external_mcp.rst``).
* **Names are reserved:** a connected app's name is never a keyless client's label, even while its key file is
  missing or unreadable (``LocalCliServer.reserved``).
* **Codes:** machine ``code`` fields exist only in the CLI envelope, never in tool results.
* **Platforms and launcher:** macOS first. Linux shares the Unix-socket code (host tests only). Windows refuses to
  listen (named pipe not implemented). The launcher is ``scripts/angelo/angelo_cli.py`` run by the bundled Python in
  isolated mode (``-I``); it is not installed on ``PATH``.
* **Names and protocol:** the command, launcher, runtime directory, socket name, messages and HMAC contexts use
  ``angelo``. Earlier variable spellings remain deprecated fallbacks (``native_editor_build.rst``, "Legacy names").
  ``angelo-cli/2/...`` is CLI protocol 2: a protocol 1 client and a protocol 2 app (or the reverse) answer
  ``incompatible`` (exit 9). Use the launcher bundled with the running app.

Architecture
============

::

   coding agent shell -> angelo_cli.py -> cli_client.py (stdlib only, no bpy)
   external AI app    -> angelo_mcp.py -> external_mcp (stdlib only, no bpy; its app key)
     -> Unix socket in a private runtime directory: peer UID check,
        token challenge (+ app proof), one framed JSON request per connection
        -> external_cli.LocalCliServer connection thread (no bpy)
           -> external_service.RequestService: commands, bounds, rate limit
              -> bounded WorkQueue -> external_host.ExternalHost.drain in ui.pump (main thread)

==================================== ==========================================
Module                               Responsibility
==================================== ==========================================
``external_cli_protocol.py``         Framing, token proofs, exit codes, envelope,
                                     endpoint/runtime-directory checks, escaping
``external_cli.py``                  Listener, peer credentials, endpoint
                                     record, connection limits, reserved app
                                     names, Windows refusal
``file_owner.py``                    "Is this file this user's own?" on every
                                     platform (owner ID; owner SID on Windows)
``external_requests.py``             Path projection, work queue, rate limit,
                                     audit, the label-to-conversation map
``external_service.py``              Per-connection command handling
``external_host.py``                 Main thread: preference and kill switch,
                                     listener lifecycle, External conversations
                                     (``cli/clients.json``), status
``cli_client.py``                    Client used by the launcher
==================================== ==========================================

Connection threads never touch ``bpy`` or a controller. A defect in the CLI host stops the listener and is shown in
Preferences; it never breaks ``ui.pump``.

Using the CLI
=============

1. Preferences > AI Providers > **Allow apps on this computer to reach Angelo** (off by default; stored with the
   add-on preferences). The status line below the toggle reports listening, off, the kill switch or why listening
   failed.
2. Run the client with Angelo's bundled Python::

     PY=/Applications/Angelo.app/Contents/Resources/5.3/python/bin/python3.13
     angelo() { "$PY" -I /path/to/angelo-studio/scripts/angelo/angelo_cli.py --client my-agent "$@"; }

     angelo status

3. To work with the scene, connect the agent in Angelo (Preferences > AI Providers > External AI apps) and use its
   MCP tools (``external_mcp.rst``).

The client finds ``<state>/cli/endpoint.json`` through ``--state-dir``, then ``ANGELO_STATE_DIR``,
``BLENDER_USER_CONFIG``/``angelo-agent``, ``BLENDER_USER_RESOURCES``/``config/angelo-agent``, then Angelo's
default user-config directory (``app_identity.rst``). The label is ``--client``, else ``ANGELO_CLIENT``, else
``external`` (1–32 of ``[A-Za-z0-9._-]``). Every variable also accepts its deprecated spellings (``product_env``).

Transport and authentication
============================

* **Off by default.** The listener runs only while the toggle is on, the
  ``angelo_agent`` add-on is enabled (the default layout enables it) and
  ``ANGELO_DISABLE_LOCAL_CLI=1`` is not set. Turning the toggle off, setting
  the kill switch or disabling the add-on withdraws the endpoint record and
  socket, fails queued work as ``unavailable`` and ends every connected app's
  access (``external_access``, ``external_mcp.rst``, "Revoking access"). Quitting with access on removes both files through an ``atexit``
  hook that touches files only.
* **Runtime directory:** ``ANGELO_CLI_RUNTIME_DIR`` (absolute), else
  ``$XDG_RUNTIME_DIR/angelo``, else per user ``$TMPDIR/angelo-<uid>`` on macOS
  and ``/tmp/angelo-<uid>`` elsewhere. Created ``0700``; ``lstat`` must show a
  real directory owned by the effective UID with no group/other bits, or
  listening is refused. The owner question is asked through
  ``file_owner.owned_by_this_user`` here and for the endpoint record and an
  app's key file: the client and an app's launcher run these checks on any
  platform, and Windows has no ``os.geteuid`` (it answers with the owner SID
  against the process's own; the mode bits then refuse there, as "unsafe",
  where an ``AttributeError`` used to end the command). The socket is ``angelo-cli-<digest of the state
  directory>.sock``, bound under umask ``077`` and set ``0600``; a path over
  103 bytes is refused with advice to shorten the runtime directory.
* **Endpoint record:** ``<state>/cli/endpoint.json`` (directory ``0700``,
  file ``0600``, ``O_EXCL|O_NOFOLLOW``) holds protocol, socket path, PID,
  instance nonce, a 32-byte per-launch token, and the build's ``angelo_version``
  and ``agent_protocol`` (``external_mcp.rst``, "Two Angelo builds"). A leftover
  record is removed only when its PID is dead (or is this process); a live other
  PID refuses to start, naming that build and its PID, and is never replaced.
  A leftover socket is unlinked only if it is this user's socket and either
  its record named a dead PID or nothing accepts connections on it.
  Angelo's file tools cannot read the record: saved projects exclude the state
  directory and unsaved workspaces refuse ``..``.
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
  8 MiB; an oversized result is ``tool_failed``, never truncated. Each frame must arrive within 10 s (an absolute deadline, so a
  slow sender cannot hold a slot). At most 8 concurrent connections (others
  get ``busy``), 16 queued main-thread items, and 20 commands per second per
  label. One connection carries exactly one request.
* The transport opens no TCP, UDP or loopback listener (checked by a host
  test of ``external_cli.py`` and by ``lsof`` in the native fixture, which
  also checks that no team sign-in is waiting: the team sign-in callback is
  the app's only network listener and exists only during a sign-in).

Commands
========

Every command writes one JSON envelope to stdout (``--format text`` for people) and, when not ``ok``, a one-line
note to stderr. JSON output uses ASCII escapes; text output escapes control, ANSI, bidirectional and newline
characters in untrusted values.

``angelo status``
   Instance, toolset, document digest and the label's conversation: its approval policy, whether it is still bound
   to the open document, whether it is busy, pending calls and mutation-ledger use. Creates no conversation.

``angelo session new``
   Creates a fresh ``External: <label>`` conversation, records the rotation in both transcripts and routes the label
   to it. Refused as ``busy`` while the current one has calls in flight.

``call``, ``tools`` (sent by an older client)
   ``refused`` (exit 4): no client runs a tool; the message says how to connect an agent through Angelo's agent.

``agent`` (a connected app's launcher only)
   Its commands to Angelo's agent (``external_mcp.rst``, "Tools"); refused for a label without an app key.

Envelope (all fields always present)::

   {"cli_protocol": 2, "ok": true, "code": "ok", "exit": 0, "retry": "never",
    "toolset_version": 17, "document": "<sha256>", "conversation": "<uuid>",
    "call": null, "result": {...}, "message": "..."}

==== ================ =========================================================
Exit Code             Meaning and the only safe next step
==== ================ =========================================================
0    ``ok``           Terminal, no error. Outcomes stay in ``result``.
1    ``tool_failed``  Angelo's agent's step reported an error (an app's
                      ``agent`` command); read the conversation.
2    ``invalid``      Unknown command, argument or JSON error. Nothing was
                      done.
3    ``not_applied``  Not applied (no connection, a refused attachment).
4    ``refused``      Not available to this client: ``call`` and ``tools`` for
                      every client, ``agent`` without an app key.
5    ``stale``        The document or conversation changed.
6    ``pending``      Still running; read again.
7    ``uncertain``    The outcome could not be confirmed.
8    ``unavailable``  Not running, access off, authentication failed, or
                      access turned off while waiting.
9    ``incompatible`` CLI protocol mismatch.
10   ``busy``         Queue, rate limit, waiting reads or a non-responsive
                      main thread. Nothing was done; retry later.
==== ================ =========================================================

``retry`` is ``never``, ``poll``, ``after_observe`` or ``same_id_later``. Codes are derived from host state,
never from message text. Results are projected before leaving Angelo: ``path``, ``filepath``, ``file_path``,
``directory`` and ``root`` keys holding absolute paths become ``"<withheld>"``, and the state, connections, project,
temporary and home directories inside any string become ``<angelo-state>``, ``<angelo-connections>``,
``<project>``, ``<temp>`` and ``<home>``.

Audit
=====

``cli/audit.jsonl`` (``0600``, trimmed to 256 KiB) records time, label, peer UID/PID, command, tool, argument SHA-256
and code for each refused ``call``; never arguments or results. Transcripts and the invocation journal remain
authoritative.

Validation
==========

* Host (no Blender; Linux CI with ``--max-skips 0``): ``test_native_external_cli_transport.py`` (framing, proofs,
  private directories, endpoint safety, real sockets: token mismatch, impostor listener, peer mismatch, oversized
  and slow frames, connection limit, stale records, stop, exit hook, Windows refusal, no network listener),
  ``test_native_external_cli_service.py`` (commands, codes, projection, bounds, audit of a refused call, client
  usage and escaping) and ``test_native_external_cli_host.py`` (real ``Controller`` through the real listener and
  client: status, ``session new``, a refused call reaching no controller, toggle-off, kill switch, failure latch).
* Native GUI fixture with private HOME, TMPDIR, user config, state, accounts and runtime directories::

    python3 tests/python/angelo_agent/test_blender_external_cli.py \
      --binary /absolute/path/to/Angelo --out <output directory>

  It enables the toggle, runs real client processes (status, a refused call and tools, text output, a forged
  token, ``session new``), turns access off, tests the kill switch, quits while listening, and requires an unchanged
  scene, no provider start, no TCP listener and clean logs.

Known gaps
==========

* Linux and Windows are not natively validated; Windows does not listen.
* Peer credentials are verified natively only on macOS; Linux ``SO_PEERCRED`` is exercised by host tests on CI.
* Same-user processes can read the token; approvals in Angelo remain the boundary.

Security analysis
=================

* **Threat model.** Same user, local machine. Same-user processes can already read the profile and token and
  usually Angelo's memory, so the transport keeps out other users, remote hosts and accidental exposure and adds no
  authority. It does not defend against same-user malware.
* **Token theft** yields socket access only: a client without an app key can read status, nothing else.
* **Confused deputy.** Angelo acts with Blender's privileges, so an outside agent could try to use it to escape its
  own sandbox. Hence off by default, no tool call from outside, reserved app names, and a connected app's work only
  through Angelo's agent under the person's approvals.
* **Prompt injection.** Names, text and results are data, escaped in both output modes and never interpolated into
  instructions; an app's words reach Angelo's agent framed as untrusted (``external_mcp.rst``).
* **Billing.** A client without an app key triggers no provider inference.
