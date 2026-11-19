..
   SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
   SPDX-License-Identifier: GPL-2.0-or-later

###################################
Headless agent: a hidden-window app
###################################

Status: 2.0.1, ``feature/v2.0.1-mcp-headless``, macOS first. Approvals are relayed **read-only**: only the person
approves, in Angelo's window (``angelo_show``); the app may only decline. Approving from the app was designed and
refused by its break-it review (`Later: approving from the app`_).

An external AI app (Claude Code, Codex or any MCP client) works with Angelo's agent on a ``.blend`` without Angelo's
window, exactly as it does with the window (``external_mcp.rst``, "Working through Angelo's agent"). Maintainer: "Headless
to me just mean don't open the window, but it should still interact with Angelo." Agreed design (coordinator,
2026-11-18): **the real Angelo app on that file, its window kept off screen.** Connections, model, routing, policies,
instructions and checks are the window's own, because it *is* the app: no new credential path, no second agent stack.

.. contents::
   :local:


Shape
=====

::

   MCP client --stdio--> angelo_mcp.py (the launcher, one per client session)
     angelo_open_file {"file", "caps"}   (an agent tool: external_mcp_hidden)
       -> refused while any other Angelo of this user runs (the app uses that window)
       -> working copy beside the source (hidden_copy.claim): the session's output
       -> <Angelo binary> --disable-autoexec <working copy>   (a GUI process, not --background; its own session)
            environment: ANGELO_HIDDEN_AGENT=1, the session id, the launcher's pipe fd, the source path, the caps,
            the app's label (headless_window.hidden_environment); read once at register, then removed
            pipe: its first line names the session (else the app ends); then close / show / decline
            register (Python, before the window shows): take the state folder's GUI lock or end, hide the app
     angelo_send / angelo_read / angelo_stop / angelo_scene / angelo_viewport / angelo_image / angelo_settings
       -> the hidden app's CLI socket, the same path as the person's window (external_agent)
     angelo_answer_approval {"card", "answer"}  -> decline only; approve answers needs_person
     angelo_show {}        -> the window comes up for the person; from then on it is their window
     angelo_close_file {}  -> the hidden app stops its turn, saves beside the source and exits

Everything after the launch is the window's code path: ``ExternalHost``, ``AgentChannels``, ``console.submit``, the
Controller, its approvals and Undo. The app is hidden, not changed.

**Launch flags (review S6).** ``--disable-autoexec`` only: a ``.blend``'s own Python never runs, because the app chose
the file, not the person. Everything else is the person's own app: their add-ons load and their connections and
network settings apply (no offline mode: the agent needs its connection), as in their window.

**What the hidden flag is (review 2, S1).** ``ANGELO_HIDDEN_AGENT`` and the session id are environment variables, read
in Python; no C flag exists. The flag grants nothing: no approval and no permission. It only narrows (the endpoint
answers one app, the app hides and ends with its launcher). A process that inherited it would hide and then end,
because the pipe it names must start with that session's id from the launcher that made it, and the app removes the
variables from its own environment at register and makes the pipe's descriptor non-inheritable.


Lifecycle
=========

* **Start** only from ``angelo_open_file``, after the app's connection and the computer's kill switch are checked; the
  person's own *Allow apps* switch is Angelo's to apply, in the hidden app: with it off the app ends at once and the
  start says so. Refused (``window_running``) while any other Angelo of this user runs, read from the process list (a list that
  can't be read counts as one running), and while a window an earlier ``angelo_show`` released still runs (only a
  session the launcher still owns is exempt). Refused (``read_only``) when the file's folder can't be written.
* **One GUI Angelo per state folder (``headless_window_lock``).** Every GUI Angelo on macOS, the person's window
  included, takes an exclusive ``flock`` on ``gui.lock`` in the state folder at register and holds it for its life
  (elsewhere no hidden session exists, so a launch consults no lock: ``headless_window.supported``); the kernel
  releases it however the process ends, so it is never stale. Its record holds the pid, the process's start time and
  the hidden session's id (none for a window). A hidden session that can't take it at once ends before it does
  anything: this covers two launches in the same instant, which the process check alone could not.
* **One app.** While hidden, the CLI endpoint answers only the app that started the session
  (``headless_window_app.foreign``); others are told it works for another app.
* **Every end** (``angelo_close_file``, the launcher's pipe ending, SIGTERM, ``IDLE_EXIT`` of 10 minutes without a
  call, a cap): stop the running turn as the person's Stop does, withdraw its cards (declined: nobody will answer
  them), wait up to ``STOP_WAIT`` (15 s) for the turn to settle, save beside the source, quit
  (``headless_window_app._finish``). Nothing is left running. **A save that fails at the end never quits**: the window
  comes up for the person with the unsaved work instead (Angelo's quit operator does not ask).
* **Saves.** The working copy beside the source is already named as the session's output (``<name>.angelo-<n>.blend``),
  and the hidden app saves it in place as it works: within ``SAVE_IDLE`` (2 s) once the agent is idle, and at least
  every ``SAVE_BUSY`` (20 s) while it works, so a crash or a kill keeps the edits up to then; and at every end. A
  change counts once the app has sent the agent work (or a turn ran): Blender's dirty flag, or a depsgraph update
  that changed data (a scene, object, mesh, shape key, particle setting, material and the like; never the file's own
  load, a selection or a playback frame), so an unedited copy is never saved. Nothing
  is saved while a card waits: what its call staged is not approved. No ``.blend1`` (the save-version preference is 0
  for that save, then restored with the preferences' own dirty flag, so the person's preferences are not rewritten).
  Never the source: a save is refused unless the open file is a different file in the source's folder. Saves run
  through ``edit_tool_hold.guarded``: never while an operation is half-made. The launcher removes the copy only while
  it is still the unedited one it made; an edited copy is the output.
* **One authority before any signal** (review 4, M-A). Whether a process is still a hidden session is read from the
  state folder's lock record alone: it must name that session id and that pid, and the pid must verify (this Angelo
  binary, the same start identity: its start time as ``ps -o lstart=`` prints it with ``LC_ALL=C`` and ``TZ=UTC``, so
  writer and reader agree whatever their locale and time zone, and a record an earlier build wrote still verifies).
  Both are read from the kernel (``process_identity``: ``sysctl`` and libproc on macOS, ``/proc`` on Linux), never by
  running a program: every launch reads its own at register, on the main thread, and the 2.0.1 final's ``ps`` there
  failed the offline GUI fixtures, which refuse any process at start-up. The launcher (``HiddenWindow.live`` before
  each SIGTERM or SIGKILL), the person's launch and End it all re-read it first
  (``headless_window_lock.live_session``). A record that names no session (the person's window), can't be read or
  doesn't verify is never a reason to signal: the launcher lets the process go. The identity is read before the lock
  is taken, so a record is written at once, and a released lock's record is cleared.
* **The person's own launch reveals the session** (``headless_window_lock.hand_over``). While a hidden session holds
  the state folder, the person's launch sends it SIGUSR2: it comes up as the person's window, its turn, cards and chat
  as they are (nothing is declined or ended), and the second launch ends, so one Angelo holds the folder. The same
  happens when the person launches Angelo from Finder or the Dock (LaunchServices reopens the running hidden app
  rather than starting a second one: its reopen AppleEvent reveals it, ``headless_window_appkit.on_reopen``), opens a
  file in it from Finder (a file the session didn't open loads: ``_loaded``), or it becomes visible any other way.
  While the reveal is awaited the launch keeps trying the lock, so a session that ended instead never shows a busy
  dialog. A session that hasn't
  come up within ``REVEAL_SECONDS`` (10 s; a long operation can hold its tick) is **never ended on a timer**: a
  dialog names the app and the person chooses *End it* (SIGTERM: it stops, saves beside the source and quits; after
  ``END_SECONDS``, 45 s, still that very session: SIGKILL) or *Quit* this launch (which withdraws the reveal it
  asked for: SIGUSR1). A record that doesn't verify (re-read
  for up to 2 s while its holder may be writing it) is never acted on: the person is told, and this launch quits;
  Angelo **never starts without the lock** while a hidden session holds it. The person's own other window holding
  the lock is left alone (two of their windows are theirs to run, as in Blender).
* **After show**, in this order: the record names no session, a second SIGUSR2 or SIGUSR1 is ignored and SIGTERM is
  Blender's own (if the record couldn't be rewritten, SIGTERM keeps the session's saving handler), the session's
  handlers and App Nap activity go, and the window comes up: a regular app again, checked, and tried again on short
  ticks until the switch takes. The lock is released only by the process's exit, never before its quit.
* **Caps, visible.** Per session: working time (default 60 minutes) and spend (dollars; tokens when set), counted
  across every conversation the session had (a new chat counts with the old one), by the one meter a goal round's
  budget reads too (``goal_probes.usage_now``): the usage ledger, then what the running request has reported that the
  ledger doesn't hold yet, so a long turn stops at its cap before its receipt is posted. The ledger is read first (a
  request is marked finished before its receipt is counted), so a request is never counted twice. A ledger that can't
  be read counts as past the spend cap. Every
  ``angelo_read`` state carries ``session: {hidden, caps, used, output, ending}``. The ceiling is ``DEFAULT_CAPS``;
  the app can only lower it (``caps_from``). The launcher also ends the session on its own timer at the time cap plus
  ``QUIT_SECONDS``, whatever the app does (``HiddenWindow.timer``).


Relayed approvals
=================

With the window, approvals stay in Angelo. Hidden, nobody can press a card, so each card is relayed to the app **in
full, read-only** (``headless_window_relay.relayed``): ``angelo_read``'s ``state.approval`` gives the tool, why it asks
(``approval_wait.reason_text``), the exact script (its full text) or the edit's exact arguments, the choices the card
offers, and ``card``: a name for this exact card (a hash of the call and its invocation digest) that can only
decline. ``state.needs`` says the person must approve.

* **Decline** (``angelo_answer_approval {"card", "answer": "decline"}``): narrowing, so safe from a model. It travels
  on the launcher's own pipe, declines that card only if it is the one waiting, and is recorded in the chat:
  *Declined <tool>, answered through <app>*.
* **Approve** answers ``needs_person``: the app tells its human and calls ``angelo_show {}``. The hidden app becomes a
  regular app again (Dock icon, menu bar), unhides and comes to the front with the card; its cards' shields start
  again (``approval_arming.restart``: a card drawn while nobody saw it has spent its shield), so the person's first
  press can't land on Approve. From then on it is the person's window: their approvals, their file, the app's calls go
  to it as to any window. It keeps the state folder's lock as a window (no session); the pipe, idle and caps no
  longer end it; the launcher lets it go and never ends it or removes its file.
* **Standing consents** made earlier in Angelo apply as in the window: *Always allow* (global, Auto chats, connected
  apps' chats included once the person gave it in 2.0.1 or later) and the conversation's *Allow for this session*
  cover Angelo's agent's scripts there while hidden too, because it is the same app and the same conversation rules.
  The relay never creates one.

Later: approving from the app
-----------------------------

The first design (``angelo_answer_approval`` approving with a single-use id bound to the call's digest, through a
narrow C relay scope) failed its break-it review (2026-11-18, ``mcp-headless-design-review-1``):

* **M1.** The answer was a model-callable tool with the id in the model's context: an MCP client that auto-approves
  tools, CI, or injected content would approve with no human; even an honest model paraphrases the script. A later
  design keeps the id in the launcher and asks the app's human through MCP ``elicitation/create`` with Angelo's own
  text; only an elicitation accept reaches the pipe; clients without elicitation use ``angelo_show``.
* **M2.** A new grant nobody granted: it needs an off-by-default per-app consent pressed in Angelo (and read by C),
  with ``RequireAskForEdits`` and ``AllowAutoApproval: false`` forcing the person.
* Also needed then: a C ``hidden_session`` latch from argv parsed only before ``--``, cleared on the first window show
  or real input; the digest bound to document and scene (S7); and the S3 caveat that a C relay scope is not the only
  Python path to ``approve_tool``.


Known limits
============

* The cap ceiling is ``DEFAULT_CAPS``, not yet a setting the person changes in Angelo (review 2, S3).
* An unedited working copy left by a launcher that was killed outright stays beside the source until removed by hand
  (review 2, S6); a hidden app that ends normally saves into it, and an unedited one is removed by its launcher.
* Hiding is AppKit from Python: the activation policy Prohibited at register (before Blender's window setup) and
  windows ordered out on 50 ms ticks for the first two seconds; the macOS proof measured no window on screen and no
  focus change in 1,762 samples (review 2, S7).


Validation
==========

* Host tests: ``test_native_headless_window`` (launch command and environment, the pipe's session line, refusal while
  another Angelo or a released window runs, read-only folders, ``show`` letting the app go, decline only, the time
  cap's own timer, the launcher's wiring and tool contract), ``test_native_headless_window_app`` (every end with a
  busy turn: stopped, cards withdrawn, saved beside the source; saves as it works; decline; ``show`` with shields
  restarted; spend across chats and an unreadable ledger; the pipe's first line; failing closed without the lock),
  ``test_native_headless_window_lock`` (real ``flock`` held by another process: fails closed, no stale lock, hand-over
  signals only a verified pid, in order; the launch starts no process and takes the lock only on macOS),
  ``test_native_process_identity`` (start time, executable and this user's processes equal what ``ps`` prints, in any
  time zone or locale, without starting a process), ``test_native_headless_window_relay`` (the card in full, nothing
  in it that approves), ``test_native_inflight_spend`` (a running request's reported usage reaches the caps; counted
  once when it finishes, also when it finishes between the meter's two reads; the goal round and the hidden session
  read one meter).
* macOS proof on Mac 1 (``check_mcp_hidden_window.py``; slot queue, the "Angelo Agent Test" profile and a fresh state
  folder, never a sign-in): no window, Dock icon or focus change while hidden; its views and captures draw; a turn
  runs through ``angelo_send``; a card is relayed, declined, and ``needs_person`` on approve; ``angelo_show`` hands the
  app to the person; the output is saved beside the source; the person's launch reveals the session; idle and cap exits.
