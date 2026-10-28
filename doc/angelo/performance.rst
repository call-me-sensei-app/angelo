.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Performance
***********

This document describes how Angelo keeps agent work fast: the execution design,
the mechanisms behind each hot path, the budgets they run under, the instruction
ratchet that keeps them from regressing, and the measurements behind each
design choice with their method.

How to read the measurements:

* Unless a section says otherwise, measurements are from a macOS arm64 Release
  build, in background factory-startup processes, on a machine shared with other
  work. Wall time is therefore noisy; local optimizations are compared by
  retired instructions (`Deterministic counters and instruction ceilings`_),
  and wall or CPU time is labelled as measured under load.
* Native measurements exclude model inference, network requests and human
  approval. A local speedup is never presented as an end-to-end agent speedup;
  end-to-end figures come only from the `Live-agent latency harness`_, which
  compares the same task, model, effort, context and scene.
* Each section names its benchmark script (in ``tests/python/angelo_agent/``),
  fixture, sample counts and statistic. Windows, Linux, OpenGL and Vulkan are
  not measured unless stated.

Execution design
================

* Codex uses direct dynamic tool callbacks into Blender; Claude uses typed
  native plans. Neither goes through an MCP tool server or computer use, and
  provider stdio is not MCP. The capabilities an MCP add-on exposes are
  Blender's own Python and native APIs, so Angelo calls them directly instead
  of sending generated Python over a local socket for ``exec``.
* Provider I/O runs on worker threads; Blender work runs on its main thread
  through a bounded queue. The UI pump polls every 20 ms while a chat is busy
  and every 100 ms when idle. Dispatch wait is recorded separately from native
  execution and approval waiting.
* A parallel worker's chat runs its tool pump on a lane thread of its own
  (``worker_dispatch``), never touching Blender data there, so Blender's main
  thread never waits for a worker Blender's answer. The UI pump steps each lane
  with a baton; while the main thread is inside another chat's tools (the lead's
  view, script, merge or commit) for longer than a tick, one clock thread wakes
  the lanes once and they run on by themselves: a worker delivers its results and
  dispatches its next calls, so its next model request never waits for the lead's
  call to end. Outside such calls an idle lane waits untimed (no CPU). The main
  thread takes the lanes back before it touches a worker chat again, waiting at
  most for each lane's current section, and runs what they left for it. A chat's
  events are never dropped or block their sender: past half its queue they wait
  in order, a stream's fragments joined (``event_queue``). Measured on Mac 2 (one worker, a lead holding the
  main thread 3 s): a call asked as the hold began was answered after 3.01 s
  before, 0.25 s after a Python script (the lane shares Python's lock with it)
  and 0.02 s during a render; 6-7 and 14-17 worker rounds ran during the hold.
* A Python script's precondition (its approval's structural/transform guard,
  read two or three times a call and, for a worker, carried both ways across
  its connection) is one digest of every object's identity, world matrix, data
  and modifier count, read in bulk (``script_precondition``), not a tuple of
  about twenty values an object. Measured with ``test_blender_concurrent_edit``
  (lead and one worker, 3,072 objects in 64-object batches, Mac 1 development
  build, two runs each): the worker's wait before each edit went from 117-121
  to 55-57 ms and the run from 8.73 to 6.05 s with shipped settings; 11.0 to
  8.5-8.7 s with the suite runner's journal verification on. The lead's and the
  worker's chat code still share one Python lock in the lead's process, so their
  edits mostly alternate.
* Runtimes stay warm and resume their native session IDs rather than
  restarting per tool. Codex subscription chats share one app-server per
  connection (`Shared Codex app-server`_).
* Batches of up to 64 validated operations take one approval and one Undo step.
* Mutations return native read-back (actual names, positions, dimensions and
  counts), so a model needs no separate inspection round to verify an edit.
* The first message of a turn carries a small current scene observation with
  exact revisions for the active and selected objects, so a first edit needs no
  discovery round (`Turn context v2`_).
* A Claude plan may finish immediately after verified native application. When
  host checks prove every edit, the host composes the final summary
  (`Fewer model rounds per edit`_).
* Streamed text updates the native text view in place instead of rebuilding
  the transcript per token; a full rebuild remains the fallback when an
  earlier message is replaced.

Screen-capture strategy
=======================

Blender already draws the person's live viewport natively; no agent screenshot
stream is needed for the person to see the result. The model gets structured
scene state by default and an image only when visual information is needed.

The visual observer, ``angelo_view`` (``agent_execution.rst``), draws only the
3D scene into a bounded offscreen buffer (256–1024 pixels), reads the GPU
texture directly and encodes PNGs on worker threads; it never captures the
window, editors or credential and settings UI. It creates no temporary file or
Blender Image: the only file is the chat's private reference PNG that provider
delivery requires. A small cache serves identical unchanged requests without
drawing or encoding again. Views are requested at checkpoints, not one per
object edit (`Visual observations`_).

Continuous image uploads are not lower-latency by default: they add GPU
readback, encoding, bandwidth, input processing and context cost, and the
supported coding-agent sessions are not continuous video inference streams.
Local preview streaming and model visual observations are separate features.

Runtime prewarm
===============

A message that launches its chat's runtime waits for the launch before its first
model request: Claude Code's probes, launch and ``initialize``, or a Codex
app-server's start. So the launch starts while the message is written
(``runtime_prewarm.py``), not when it is sent, and not for every visible chat:
an idle Claude Code process holds about 190 MB resident (measurements below).
With empty isolated profiles and no user turn, Claude Code takes a median
0.33–0.35 s from launch to its ``initialize`` reply (about
0.5 s with the version and sign-in probes) and about 0.1 s for a Codex
app-server, on a loaded machine.

* **When.** The UI pump (every 100 ms when idle; ``prewarm_ui.py``) reads the open chat editor's
  draft. A runtime starts when the person changes a draft that is not empty
  (typing, pasting) in a chat whose next message would launch one: no runtime
  yet (a new chat, or one whose runtime was closed), not busy, not an External
  CLI conversation, no voice, no changed document or queued connection change,
  a ready connection, and no model-context switch or context rebase that the
  message would make first (either closes any runtime). A draft that is only
  shown (restored at launch, or on switching chats) starts nothing.
* **What.** Exactly the launch the send would make, for the request the message
  will be (``PrewarmContext``: its options, Auto's model, tools, cache TTL and
  native session): Claude Code's sign-in check, version probe, launch and
  ``initialize`` (``ClaudeProvider.start_prewarm``); on a Codex connection with
  an app-server of its own (API key, team, another provider), the app-server
  and its ``initialize`` (``CodexProvider.start_prewarm``), not its checks or
  thread. ChatGPT plan chats share an app-server their opening starts
  (`Eager openings`_): nothing starts for them here.
* **Nothing is sent.** No message, thread or turn: the person's message is the
  first model request, as before. A failed prewarm shows nothing; the send runs
  the same checks and shows their error.
* **Adoption.** The send uses the runtime (``runtime_prewarm.adopt``; the
  receipt counts ``runtime_prewarmed``) and skips the checks the prewarm ran. A
  send during the launch waits for it (``Launch.settle``, at most 10 s; a
  healthy launch takes about 0.5 s, and a stuck one is abandoned for a launch of
  the request's own) instead of starting a second process. A Claude session
  whose options, instructions, tools or TTL differ from the request's, or that
  was launched for another native session, is closed and relaunched
  (``ClaudeProvider._turn``); a Codex app-server whose command, environment or
  folder differ is closed and replaced (``CodexProvider._adopt_prewarmed``).
  Reasoning effort is none of these: a Codex turn states its own, and Claude
  Code changes a running session's effort before the turn with the
  ``apply_flag_settings`` control request (``{"effortLevel": ...}``; its SDK
  handler sets the session's effort, read in the 2.1.292 and 2.1.293 binaries,
  the floor of the gated range; ``claude_transport.apply_effort``). A session
  that answers with an error, or not within ``EFFORT_SECONDS`` (10 s), is
  relaunched with ``--effort`` as before. Not exercised against a live model.
* **Bounds.** At most one prewarmed runtime in the app
  (``MAX_PREWARMED``): 181–192 MB for Claude Code, 53 MB for a Codex
  app-server before its thread (measured below); writing in another chat
  closes the earlier one. It is closed when the draft is emptied, two minutes
  (``IDLE_SECONDS``) after the draft last changed (and starts again only when it
  changes), when the launch key changes (``runtime_prewarm.key``: epoch,
  connection, native session and slot, options but effort
  (``runtime_prewarm.EFFORT_FREE``), TTL; a matching runtime starts at once), and with the chat's runtime for any other reason (a new chat,
  another connection, a sign-in, voice, the app closing). A closed one is never
  reused.
* **Worker chats (design A′).** A worker chat's provider runs in its worker and
  its runtime child in Angelo, so nothing of it starts in Angelo's own process:
  the mirror's record counts in the rules above (draft, idle bound, launch key,
  ``MAX_PREWARMED`` across every chat), and ``chat.prewarm`` asks the worker's
  chat to start or release its own prewarm (``worker_mirror_prewarm``; launched
  through Angelo's runtime service, C5). An open also starts one, because a
  message (the brief, a relaunch's) follows it. For a ChatGPT plan chat the
  runtime service starts the shared app-server's process as it opens
  (``RuntimeService.prewarm``), and the worker's first launch adopts it.
  Before A′ a worker chat ran in Angelo and got no prewarm for its brief (it
  was never the chat being written in); a plan chat leased Angelo's already
  running app-server. A worker's unadopted prewarm ends after ``IDLE_SECONDS``.
* Tests: ``test_native_runtime_prewarm.py``, ``test_native_worker_chat_warm.py``.

Measured with ``runtime_launch_check.py`` (unbilled: the real controller and
runtimes, a local mock that answers each request at once, a dummy key, every
other host behind a dead proxy), seven trials per arm with
runtime versions and arms alternating, two seconds of typing before Send, load average
2.8–3.0 for Claude and 3.4–3.6 for Codex. Medians, milliseconds; p75 in
brackets:

.. list-table:: A new chat's first message
   :header-rows: 1

   * - Runtime, arm
     - Send to first request (Codex: to its open thread)
     - Send to turn end
     - First keystroke to warm
     - Resident memory
   * - Claude Code 2.1.292, launched at Send
     - 271 (274)
     - 293
     -
     -
   * - Claude Code 2.1.292, prewarmed
     - 124 (128)
     - 148
     - 275
     - 184 MB
   * - Claude Code 2.1.301, launched at Send
     - 279 (288)
     - 302
     -
     -
   * - Claude Code 2.1.301, prewarmed
     - 122 (126)
     - 150
     - 277
     - 191 MB
   * - Codex team (agent runtime 0.162.0-alpha.17.2), connect at Send
     - 98
     -
     -
     -
   * - Codex team, prewarmed
     - 37
     -
     - 108
     - 53 MB

2.1.258 and 2.1.302 gave the same picture (``ai_providers.rst``, "Claude Code's
range"). Every Claude trial sent one request per turn and none before Send;
every prewarmed request adopted its runtime (``runtime_prewarmed``). The saving
is the launch on the request's path, about 150 ms for Claude Code on an API-key
connection here and about 60 ms for a Codex team chat's connect. Not in it: a
subscription's ``claude auth status`` probe, which a prewarm also moves off the
path (not measured: it reads the sign-in), and TLS to the provider or gateway,
which no prewarm opens because it sends nothing. "First keystroke to warm"
includes up to 100 ms before the pump sees the keystroke, so a message sent
within about 0.3 s of its first keystroke waits for the rest of the launch, and
never starts a second one.

Reasoning effort policy
=======================

Each model has a default reasoning effort (``provider_registry.py``), selectable
per chat in the model menu; Fast is an independent provider service tier, not an
effort alias. Routing between models and escalation to a stronger model are
described, with their measured gate, in ``model_routing.rst``. Escalation keeps
the person's selected connection, the native approval gate and the mutation
ledger.

Deterministic counters and instruction ceilings
===============================================

Wall time on a shared machine moves with every other process, so local
optimizations are compared by the work a process does: retired instructions.
``perf_counters.py`` (standard library only) reads ``ri_instructions`` and
``ri_cycles`` from ``proc_pid_rusage(getpid(), RUSAGE_INFO_V4)``; the
``struct rusage_info_v4`` layout comes from the macOS SDK's ``<sys/resource.h>``
and is pinned by a host test. CPU time comes from ``time.process_time``, because
the struct's own CPU times are mach ticks. Other platforms report instructions
and cycles as unavailable, never zero. A spin loop of 10\ :sup:`9`
two-instruction iterations reads back 2.005 × 10\ :sup:`9` instructions and
1.008 × 10\ :sup:`9` cycles. ``perf_counters.read_thread_counts`` reads the
calling thread's own counts (xnu's ``thread_selfcounts``); it matches
``proc_pid_rusage`` within 0.02% on one thread and ignores a busy second one.
Process counts cover every thread, user and kernel work alike, so thread-pool
waiting, page faults and first-use caches still add some variation; counts
compare the same binary on the same CPU architecture only.

Every native benchmark reports, per operation, wall time (``samples_ms``,
``median_ms``, ``p95_ms``) plus CPU time, instructions and cycles. Measured
spread on an Apple M4 Pro under a 1-minute load average of 35–47:

* A fixed pure-Python workload, 20 repeats in one process: coefficient of
  variation 0.15% for instructions, 8.5% for CPU time and 11% for wall time.
* 31 read-only queries, revisions, deltas and snapshot operations of
  ``benchmark_blender_agent_execution.py``, ten runs in separate processes: the
  run-to-run range of each operation's median is 0.6% for instructions (median
  operation; 75th percentile 0.9%), 27% for CPU time and 44% for wall time.
  Small operations of 12–28 million instructions and operations that write
  files vary more.
* Staged programs stop each fused poll after a wall-clock work budget
  (`Fused staging polls`_), so under load one program splits into more polls,
  each paying a stage write and reload. For measurement only,
  ``benchmark_fixtures.instruction_work_clock`` replaces that clock, through the
  backend's ``operation_quantum_clock`` hook, with the main thread's retired
  instructions at a fixed 2 × 10\ :sup:`10` per second. Programs then split
  the same way in every run (one poll at 100 and 2,000 objects, two on the
  99,856-vertex grid), and their run-to-run instruction range falls to a few
  percent. The product keeps its wall clock; ``*_work_clock`` operations appear
  only in benchmark reports.

Instruction counts do not replace wall time for user-visible latency: I/O, lock
waits and parallel speedups do not show up in them. The sections below give
both where both matter.

Instruction ratchet as a merge gate
===================================

The ratchet keeps each operation's instruction count from creeping up.
``perf_ceilings.json`` holds one ceiling per operation and platform key
(``darwin-arm64``) with a tolerance band: 5% by default, twice the observed
run-to-run range where that is larger (up to 25%), and at least 10% for
operations that write files, whose kernel work makes them vary more. The
snapshot three-write sums (below) are the one exception, decided by the
maintainer on 2026-10-27: their tolerance is twice their observed run-to-run
range with no 5% and no 10% floor. When a ceiling is recorded from several
runs of one build, the ceiling is the highest run median and the range is
(highest − lowest) / lowest of those medians
(``perf_ratchet.restamp_tolerance``). ``perf_ratchet.py`` compares a benchmark
report with the ceilings:

* ``regressed``: the median exceeds ceiling × (1 + tolerance); the check fails.
* ``improved``: the median is below ceiling × (1 − tolerance); ``--update``
  lowers the ceiling to the measured value.
* ``within``: anything in between changes nothing, so noise inside the band
  can neither fail the check nor ratchet a ceiling down step by step.
* ``new`` operations are recorded by ``--update``; a ceiling rises only with
  ``--update --allow-raise``, with the reason for the extra work stated in
  review.
* ``unstamped``: a measured operation with no ceiling whose tolerance needs a
  run-to-run range (the three-write sums). ``--update`` cannot record it from
  one run, so the gate fails on it until ``perf_rebaseline.py`` records it
  from several runs of the build being stamped::

     python3 tests/python/angelo_agent/perf_rebaseline.py RUN1.json RUN2.json RUN3.json RUN4.json \
       --operations 'snapshot/*_three_write_sum'

  The runs are gate receipts or benchmark reports; runs of more than one binary
  or platform are refused, so an ABAB's A and B runs never become a ceiling.
* ``rejected``: every sample of the operation had page-ins or swap, so the run
  has no valid value for it; the gate fails and the run is repeated.
* ``noisy``: the operation's own samples spread (relative range of the valid
  samples) by more than a third of its tolerance. One run cannot tell a change
  from that noise, so the absolute ceiling does not decide it: an interleaved
  ABAB against the last stamped build does (``abab_pass`` or
  ``abab_regressed``, below). An operation that reports only a median has no
  spread and stays ceiling-gated; noisy runs never lower a ceiling.

Operations are instructions unless their unit is ``bytes``: an exact size, such
as the bytes a request capture writes, recorded with zero tolerance.

Validity rules (set on 2026-10-21, before any run used them, for every build
measured):

* A sample is rejected when the process paged in from disk or the system swapped
  in or out during it (``perf_counters.rejection``; swap is read just outside
  the counted window, so it adds no instructions). Counted metrics are
  described over valid samples only; wall and CPU time keep every sample.
* Each benchmark process starts only when the 1-minute load average is below
  2.0; the suite waits up to 20 minutes (``--quiet-wait-s``) and otherwise
  fails with the load it saw. Each start load is kept in the receipt and stamp.
  ``--max-load N --conditions TEXT`` replaces 2.0 for a run whose machine
  cannot reach it (2026-10-22: Mac 1 idles at 3.5-4 with the desktop app
  open; the maintainer allowed 5.0 for the 1.0.4 restamp and its validation,
  recorded). It applies to every build of that run, ABAB included, and the
  receipt and stamp record the limit and the conditions.
* Each operation's spread is kept in the receipt, and the stamp records every
  operation's spread for the next comparison.
* Noisy operations: with ``--baseline-binary <last stamped build>`` (its
  SHA-256 or its Mach-O linker UUID must equal ``last_measured``'s; signing a
  release app changes the SHA-256 but not the UUID, so a signed copy of the
  stamped build qualifies; keep one) the suite runs each affected benchmark four times in fresh
  processes, alternating A (stamped) and B (candidate): A, B, A, B, with the
  same harness, arguments and quiet wait. ``regressed`` needs both B values
  above both A values and the mean of B above the mean of A by more than the
  tolerance; a missing value is ``inconclusive`` and fails. Order alone
  happens by chance one time in six with no change, so the magnitude condition
  is what keeps noise from failing the gate. ``perf_abab.py`` runs the same
  comparison from a receipt. Another build is admitted only with
  ``--baseline-approval "<who approved which build, and why>"``, which the
  receipt records.

``test_blender_perf_ratchet`` (a BACKGROUND native suite, run by
``run_native_suites.py --include test_blender_perf_ratchet`` on macOS arm64)
runs the execution, snapshot, stage-compression, view-check, long-session,
session-journal, native-read and chat-waiting
benchmarks against the compiled app, each in a fresh folder directly under
``/private/tmp`` with its temporary folder inside. That is the folder depth the
ceilings were recorded at: snapshot storage anchors every ancestor folder on
each write (`Private capture anchoring`_), so deeper folders cost more. The
suite passes only when no operation regressed, every ceiling was measured and
no measured operation lacks one. After a pass::

   python3 tests/python/angelo_agent/perf_ratchet.py \
     <run>/test_blender_perf_ratchet/qa/fixture/perf-ratchet.json --stamp

records the run in ``perf_ceilings.json`` as ``last_measured``: date, commit,
binary SHA-256 and a SHA-256 of the measured sources (every add-on module and
the benchmark harness). The host suite, which CI runs on Linux where no
instruction counter exists, fails while that digest differs from the
checkout's, so changed add-on or benchmark code cannot merge until the ratchet
has run on it. ``CONTRIBUTING.md`` lists what runs where.

Two kinds of operation are measured so that they report the code, not the
machine:

* Staged programs use the main-thread instruction work clock above. Counting
  the whole process let Blender's worker threads decide whether a program
  split into one or two polls (the same eight-operation program measured 1.09
  or 1.31 billion instructions).
* Request-snapshot captures are ratcheted as two operations
  (``snapshot_paired``; maintainer, 2026-10-27, for every build, both sides of
  every comparison and every document size). The kernel's page-cache copy is
  85–90% of a dense capture's instructions and its cost per byte moves between
  0.55 and 0.79 instructions with free memory and swap. Each repeat capture is
  bracketed by two reference writes of its own size beside it, each the kernel
  I/O a plain capture does (1 MiB writes, then one ``fsync``). A pair is valid
  when none of its three samples paged in or swapped.
  ``*_repeat_capture_three_write_sum`` is the median, over at least seven
  valid pairs, of reference before + capture + reference after. The bytes
  written are ratcheted exactly as ``*_repeat_capture_bytes``. The paired net
  (``*_repeat_capture_paired_net``, capture minus the mean of its two
  references) and the gross capture (``*_repeat_capture_gross``) are reported
  in every receipt and never compared, and the report keeps every pair, so
  any run can be re-scored.

  Why the sum: the kernel's work moves between a capture and the writes beside
  it, so inside a pair their costs are anti-correlated (−0.41 to −0.95 at
  500 MB). The paired net gated from 2026-10-21 to 2026-10-27; subtracting the
  references amplified that movement: one binary's per-pair nets spanned
  9–124 M instructions, 1.0.5 against itself differed by 30–43% between runs,
  and a 500 MB ABAB reported a regression the candidate did not have. In the
  same four runs the three-write sum agreed within 0.6%
  (``perf_ceilings.json``, ``three_write_sum_decision``).

  The first statistic, ``*_repeat_capture_net`` (fewest instructions of any
  capture minus the fewest of any write), subtracted two minima taken at
  different moments: on 2026-10-21 the reference write alone ranged 125–152 M
  instructions on identical code while gross capture stayed at 200.1–200.9 M,
  and the net reported a regression that did not exist.

Representative ceilings, millions of instructions on ``darwin-arm64``
(``perf_ceilings.json`` is authoritative and changes with each stamp). The
cohorts of ``benchmark_blender_agent_execution.py`` are ``default`` (100
objects), ``many_objects`` (2,000 objects sharing a node material),
``dense_geometry`` (20 dense grids, 978,516 vertices) and ``deep_collections``
(2,000 objects in a 511-collection tree).

.. list-table:: Instruction ceilings (millions)
   :header-rows: 1

   * - Operation
     - default
     - many_objects
     - dense_geometry
     - deep_collections
   * - Whole-scene search, limit 100
     - 351.7
     - 2,027.4
     - 3,512.6
     - 2,067.9
   * - Discovery page of 10
     - —
     - 225.2
     - 1,696.3
     - 229.6
   * - Rich object query
     - 71.3 (warm, exact revalidation)
     - 413.1
     - 65.7
     - 435.6
   * - Object-scoped delta
     - 59.8
     - 67.4
     - 62.4
     - 71.1
   * - One-operation staged program (work clock)
     - 833.5
     - 1,230.0
     - 820.0
     - 1,264.1

``benchmark_blender_native_reads.py`` ratchets Angelo's route of each of the
ten native reads that stock Blender can also do (``native_reads/<read>``,
`Native reads against stock Blender`_), on the dense castle scene built from
its seed, exactly as ``bench_native_reads`` times them: a release that makes a
listed read do more work does not ship. Before timing, each route must return
what the stock route it is compared with returns; the stock route's count is
recorded beside it. ``blend_listing`` runs with the product's own limits and
deadline, so a 250 MB rigged scene that the scan refused would fail the gate.

``benchmark_blender_chat_waiting.py`` ratchets the main-thread work of the
chat's waiting animation (``chat_waiting/<operation>``, `Chat waiting
animation`_): one hero and one icon frame, the heaviest reveal frame, one timer
tick and the one-time geometry of a process's first wait.

Limits: one CPU model; full-feature and lite builds of the same commit differ
by 5–25% on most operations, so ceilings compare only runs of the build they
were recorded with. No provider, prompt or model time is part of the ratchet.

Chat waiting animation
======================

The marble wait (``agent_chat.rst``, "Waiting for the first step") must not
slow anything Blender itself does, so its budget is set by what it may not
touch: the provider threads, the controller's event pump and native dispatch
run exactly as before, and the animation's only main-thread work is its own
timer tick and its part of the chat region's draw.

* **Frames.** ``marble_host``'s timer exists only while the drawn chat runs a
  turn. It asks for a redraw of the chat's ``WINDOW`` region (no other region
  or editor) only when ``marble_wait.Wait.next_frame`` says something moves,
  at most 12 times a second; otherwise it sleeps to the next scheduled chip or
  blink, capped at 0.1 s so a new event is seen promptly. Late in a long wait
  chips fall seconds apart, so most of that time asks for no frame at all;
  while steps run the blinks and the live row's clock ask (below), and
  with Reduce Motion only the clock's seconds do. The tick that sees the turn end stops the
  timer and frees the kept picture.
* **Only the marks are redrawn.** Blender redraws whole regions, so the chat's
  full draw is kept while a wait runs (``marble_frames``): it draws into an
  offscreen texture the size of the region with the marks only placed, the
  texture is drawn to the region and the marks over it. A frame the timer asked
  for draws that texture (one quad) and the marks at their places when nothing
  the picture shows has changed (the session's version, scroll, draft, the
  canvas's input serial and pointer states, the scrollbar, background jobs'
  progress, size, scale and language; never with a popover or approval card in
  view), and at most two seconds after the last full draw. Any other redraw is
  the full draw, as before; ``bypass`` draws it straight to the region for the
  suite's picture comparison.
* **Budget.** Decorative redraws have a minimum interval of two scheduling
  frames (12 Hz), leaving room for native drawing and composition. The 24 Hz
  scheduling granularity remains for the 8 Hz status pulse and transitions.
  ``marble_wait.frame_interval`` over ``marble_frames.frame_cost`` slows costly
  own draws further against the 2% allowance. That timer measures the blit and
  marks, not native region drawing/composition; the native GUI gate checks the
  complete 2% CPU budget. The animation **pauses** (no frames, the
  timer only polls) while the person interacts or cannot see it: a modal
  operator other than Angelo's own canvases (``input_policy``: orbit, pan,
  transform, box select, sculpt, area resize), view navigation by trackpad,
  wheel or NDOF within the last 0.4 s, playback, the window out of focus, and
  no mark in view.
* **The live row's status line.** The dot, elapsed time, tokens and phase
  (``agent_chat.rst``, "While a turn runs") are a mark like the raven: a frame
  redraws only the kept picture and that line. The timer asks for a frame each
  new second of the turn, which still comes while the window is out of focus
  (one frame a second), and for the breathing dot at 8 frames a second while
  nothing pauses the wait; with Reduce Motion only the seconds; a modal tool,
  navigation, playback or no mark in view pause it like the raven. So while
  steps run the wait asks for up to about 10 frames a second instead of a
  blink's five every 3.8 s, each a kept-picture frame under the same 2% pacing,
  plus the full layout the kept picture's two-second refresh already costs
  during the carving. The status line's text is part of the kept picture
  (``marble_frames.key`` includes ``live_progress.lead``), so it is laid out
  once a second and only the dot is drawn per frame. Measured on 2026-10-22
  (pre-registered frame A/B, Mac 1 with the desktop app open, signed 1.0.3
  against the branch, 7 + 8 valid interleaved runs): carving main-thread share
  2.33% against 1.29%, orbit frame time 8.31 against 8.33 ms (p95 8.75 against
  8.94 ms), at most 0.27% of the main thread after the first step; a marble
  frame's own draw 0.18 against 0.22 ms. An earlier build that drew the text on
  every frame failed the carving-share limit (2.25% against 1.92%).
  The facts it reads (``live_progress.facts``) are the controller's rows after
  the request and two floats and a flag from the provider: no lock, no event,
  nothing on the request path.
* **Geometry once.** The mark is triangulated and both chip sets are built on
  the first tick of a process's first wait, never in a draw callback; the
  raven's parts are GPU batches built once, the standing stone one batch per
  fallen chip, and only the chips in flight and the dust are rebuilt per frame.
* **Ratchet.** ``benchmark_blender_chat_waiting.py`` measures the CPU side of
  each, which is all the Python a frame runs (background Blender has no GPU).
  On a development build (Apple M4 Pro, main-thread instructions, median of
  21):

  .. list-table::
     :header-rows: 1

     * - Operation
       - Instructions
       - Wall time
     * - ``hero_frame`` (0.2 s after a tap, stone rebuilt)
       - 1.71 million
       - 0.13 ms
     * - ``icon_frame``
       - 0.19 million
       - 0.02 ms
     * - ``reveal_frame`` (every remaining chip in flight)
       - 7.7 million
       - 0.42 ms
     * - ``timer_tick``
       - 0.08 million
       - 0.03 ms
     * - ``first_wait_geometry`` (once per process)
       - 277 million
       - 12 ms

The GPU side and the whole frame are measured in the compiled app by
``test_blender_chat_waiting`` (``chat_waiting_measure``), in a silent wait
while chips fall continuously (the most frames a wait asks for). On a
development build on an Apple M4 Pro with a 120 Hz display, other work running
on the machine:

* **One marble frame**: 0.18 ms for its own draw (the kept picture and the two
  marks); 0.50 ms of main-thread CPU end to end with the window's composite and
  swap (``wm.redraw_timer`` ``DRAW_SWAP``, operator overhead included), against
  1.30 ms for the chat's full draw that a plain region redraw would cost.
* **Main-thread share while waiting**: 1.2% composed from 20.7 frames a second
  and the timer's ticks (1.4 ms a second); the main loop's iteration rate with
  the animation on and off (event simulation never idles) put it at 0.4%
  (median of five paired windows, range -0.03% to 4.2%: machine noise).
* **Orbiting**: a middle-mouse orbit pauses the animation (no frame while the
  orbit's modal operator runs); the view's frame time was 8.30 ms on and
  8.31 ms off (median, display-bound), p95 8.85 ms and 8.84 ms.
* **Frames**: at most 24 in any second; while steps run, at most four in any
  second (one blink every 3.8 s); none while steps run with Reduce Motion.
* **Reuse**: gated frame by frame by the direct cache checks
  (``test_native_marble_cache_contract``): every stable eligible frame reuses
  the kept picture; each deliberate invalidation (cold, draft, size, expiry)
  refreshes once and then reuses; an unrequested draw takes the full path; a
  reused frame matches a direct draw within the picture tolerance. The
  aggregate reused-per-full-draw ratio (formerly a 5:1 gate) is reported as
  ``marble_reuse_ratio`` only: it also counts cold and unrequested draws, so
  the lower decorative cadence failed it with no extra work (maintainer,
  2026-10-26, for every candidate from then on).

Usage meter
===========

The usage bar and its card (``agent_chat.rst``, "Usage meter") add nothing to
the path from Send to the provider: no network call, no file I/O on the main
thread, and nothing runs before a request is sent.

* **Per request.** ``usage_meter.record`` adds the finished receipt to the
  chat's totals in memory, after the request ends; the file write is on a
  background thread, coalesced, and drained when a chat closes.
* **Per frame.** The bar's parts are recomputed only when their inputs change
  (the ledger's revision, the chat's connection, the plan windows' report, the
  request in progress, the interface language); a frame otherwise reads a
  cached tuple. The card is computed only while it is open. The plan ring is
  one triangle batch per arc.
* **Live per-turn tokens.** ``turn_tokens`` is O(1): two short locks that a
  provider thread holds only to add a number.

Measured by ``test_blender_chat_usage`` (``draw_cost``: the chat's own draw time,
``chat_view.draw_seconds``, over 240 frames with the bar shown and 240 hidden,
in alternating blocks of 40 on the same chat, after the plan phase so the ring
is drawn; ``microbench``: 2,000 calls each). Mac 2 (M5 Max), CONTENDED (the
maintainer's own load runs there; not a published figure), signed 1.0.3 app
(cafdf57a103) with the add-on of the commit named:

======================================  ==============================  ================================
Measure                                 089ca104526 (56-stroke ring)    242ef0151ae (one batch per arc)
======================================  ==============================  ================================
Chat draw, bar shown (median / p95)     1.23 ms / 1.45 ms               0.75 ms / 0.95 ms
Chat draw, bar hidden (median / p95)    0.45 ms / 0.57 ms               0.50 ms / 0.69 ms
Bar's cached parts, per frame           1.9 µs                          3.0 µs
Ledger add, per finished request        6.7 µs                          9.1 µs
======================================  ==============================  ================================

Mac 1 (the billed-lock slot ``usage-meter-live``, 2026-10-22 01:33 JST, load
average about 5, same app with the add-on of 18b01af3b5a): chat draw median
0.50 ms (p95 0.55 ms) with the bar shown and 0.33 ms (p95 0.37 ms) hidden; the
bar's cached parts 2.1 µs per frame; a ledger add 6.1 µs per finished request.

The bar costs about 0.17-0.25 ms per chat redraw (its text, two pills and the
ring); the chat redraws on events, not continuously, and nothing of it runs on
the send path.

The suite times frames with ``redraw_timer(type='DRAW_WIN_SWAP')``. Hundreds of
draws without a swap (``'DRAW'``) leave Metal's deferred-free lists
(``MTLSafeFreeList``, three blocks of 64 KiB) unreleased at quit, which the
native runner reports as an allocator leak; a swap releases them, as in every
real redraw (found with ``--debug-memory`` and a phase-by-phase bisection,
``ANGELO_QA_USAGE_SKIP``).

Live-agent latency harness
==========================

``eval_live_agent.py`` drives the real controller, UI pump, approval and commit
operators in disposable factory-startup GUI processes. Each trial is a new
process and chat, so its first message is a cold start (the first chat after a
fresh launch, classified by that position and never by a cache hit) and the
rest are follow-ups in a running chat. Its tasks reuse the closed host checks of ``eval_agent_tasks.py``
where they exist: a read-only question, cube creation, a transform,
bevel-and-shade, a material, and a staged preview that the harness then accepts
through the human UI continuation. Approvals are the product default, Auto ·
bounded edits: the product approves every bounded mutation itself (typed
edits, programs, recipes, preview Accept and Discard, sculpting and flow
edits), as it does for a person, so a model's own preview Accept applies
unasked (``--approval ask`` measures Ask instead). A test-only rule answers
what still asks (recovery-review corrections, and every edit in Ask): it
approves only the typed bounded scene tools of ``HARNESS_APPROVABLE`` and
declines reviewed Python, file writes, Studio generation, delegation, preview
Accept and everything else (``NEVER_APPROVED``). ``--always-allow-scripts``
measures the opted-in path, where scene Python runs unasked in Auto. Every
trial states its approval mode (a report without one is read as Ask), and
``measured_results`` and ``bench_mcp_gate`` refuse to pool runs of different
modes, as they refuse different gate-set versions.

Results come from each request's local receipt (cold or warm, first text, first
verified scene change, main-thread block, approval wait, provider turns, Claude
typed-plan rounds, fallback categories and runtime-reported usage) and from host
checks rerun after each task. The harness reports p50 and p75 over completed,
host-verified tasks for four journeys: cold first message, warm follow-up,
prompt to first verified change, and staged preview to accepted commit. Codex
internal rounds are not observable and are never compared with Claude plan
rounds. The main-thread block covers the event-pump section that ends the
request, with its saves (``agent_execution.rst``).

A task whose turn did not end normally carries ``abnormal_end`` in its record
(``eval_abnormal_end.py``), the same for Claude and Codex: its ``reason``
(``timeout``, ``accept_timeout``, ``stopped``, ``interrupted``, ``failed``,
``job_timeout``...), the progress marks so far, the controller status at the
deadline, the age and kind of the provider's last event (``model``, or a
Claude ``keep_alive``), the last host-visible tool call and its age, the host
calls still pending, and whether an interrupt was sent and acknowledged. Its
``stream`` says what the provider streamed since the send: text and thinking
bytes and deltas, the current message's (and whether it is still open), the
delta rate over the last 10 s, and the current burst of deltas with
``structural_progress`` (a tool call, a provisional plan step or a script line
since the burst began). So a runaway message (steady deltas, no structure)
reads apart from a silent stream (no recent deltas). The product keeps no such
per-turn counts, and a Codex plan thread never emits its JSON after the
reply, so the harness counts the stream itself. In its disposable trial process
only, ``eval_stream_counts.py`` wraps the two entry points every stream event
passes (``CodexProvider._scoped_event`` and Claude's
``RoundStream._stream``) and calls them unchanged. Every
non-normal ending writes it: the harness's own Stop, any other Stop, an
interrupted or failed turn, a harness exception (the trial's
``unfinished_task``) and the app crashing, hanging or quitting mid-task (the
launcher reads the in-flight snapshot the app refreshes every 5 s). For each
such turn on a dedicated Codex profile the launcher then copies the Codex
TRACE rows of its thread and window from ``CODEX_HOME/logs_2.sqlite`` into
``trial-NN/codex-trace.jsonl`` (``eval_trace_export.py``; at most 256 KiB per
turn and 1 MiB per trial, credentials redacted), the runtime's own record of
the same window; the block's ``trace``
says what was kept. Claude Code keeps no trace log unless started with
``--debug``, so its block says so and its transcript is the record.

The trial's own ``eval-live-agent.json`` is also written at each task's send,
before the request goes out (``eval_send_record.py``, both arms): the tasks
completed so far stay in ``tasks`` and the task in flight is in
``unfinished_task`` with its stamp (``prompt_sha256``, ``test_code`` and, in
the parallel arm, ``parallel_request``), so a launcher killed after the send
still leaves the message it sent. That record carries ``in_progress``; the
launcher's end write does not. Every reader of the record goes through
``eval_send_record.finished`` and ``read``, which take a record in progress as
no record, as before (``test_native_bench_record_guard.py`` fails on a
raw reader): the bench still sets such a launch aside and runs it again, and no
report (final runs, the one-shot report and scorecard, the survey) counts it.
Only Amendment F's check of a set-aside attempt reads it, on purpose. The
in-flight snapshot and ``unfinished_task`` keep the same stamp. Every write of
the record is whole: a temporary file in the same folder, fsynced, then
``os.replace``. On Windows a replace refused while another process holds the
record open is retried, and the launcher's end write then writes in place, as
it always did.

A run passes only when a check read its result (``eval_grading.py``;
``native_coverage.rst``, "Grading contract"): closed host checks for an edit,
an answer check for a question. In version 1 of the benchmark sets the
read-only questions had no answer check, so a version-1 summary prints them as
``ungraded``, never passed. From version 2 the questions are read and the edits
are unchanged, so edit pass rates compare across versions and question pass
rates do not; latency across versions is measured, not assumed equal.

Every task sends a real message on the chosen connection, so the script prints
its plan first and refuses to run without ``--authorize-billed-runs`` and an
explicit ``--connection-id``. It copies only that connection's metadata into a
private store and never reads or copies credentials; API-key connections are
refused. ``test_native_eval_live_agent.py`` covers the guards, isolation, task
checks and statistics offline, and ``test_blender_eval_live_agent_dryrun.py``
runs the in-app driver end to end in a GUI process against a scripted stand-in
runtime (no process, network or account).

By default the harness readies nothing before a trial's first prompt
(``ANGELO_EVAL_OPENING_WAIT`` 0), so the first prompt includes the runtime
launch. ``--opening-wait SECONDS`` is a labelled option, "opening ready before
the first message": the opening is readied when the chat is bound and the
first prompt waits for it, outside the task's time. Every trial's report
records its ``opening``, including whether a shared Codex app-server was
already running at the first send (``runtime_live``; the first task's
``runtime_start`` is then ``prestarted``), and each task records the warm-up
requests made during it (``warm_ups``, ``warm_up_usage``, ``warm_up_ms``), so
no request goes uncounted.

The cold-start number of a trial (``bench_cold_warm.py``; ``final_runs.rst``,
"Cold start by launch position") is its first task's wall time and token cost
plus every warm-up of that launch, its wall time and its cost, each counted
once, and any wait for a readied opening. The runtime's launch is inside the
first task on both sides; the app's own launch is outside it on both sides. A
relaunch on a kept profile (``--returning-user``) is a returning-user start,
reported apart. Angelo's bundled, cache-keyed Codex sends no warm-up at all
(``codex_bases.warm`` refuses on a keyed process), so its cold starts have
none; a stock Codex still warms up, and that warm-up is counted.

Each task record also keeps what it was sent and graded with: the prompt's
SHA-256 and the test code's version (``bench_integrity.py``), on both sides of
a comparison. A trial is invalid, never failed, when the provider throttled it,
by one rule on both sides and runtimes (``eval_provider_limits.py``): a limit
error, or an HTTP 429 that was waited out and retried, whether the runtime
retried it (Claude Code's ``api_retry``, Codex's ``willRetry``, both counted
on Angelo's receipts as ``provider_retries_rate``) or Angelo resent it. The
rule is length-biased (more requests, more chances to meet a throttle), and one
throttle stops the run. In a comparison with stock Blender driven through an
MCP add-on, Angelo runs with the product's Always allow scripts setting and
every report states "scripts allowed"; reports also state the planned and the
valid n per side and flag a side that differs (`One-shot tests
<oneshot_tests.rst>`_, "Fairness").

Window state of GUI runs
------------------------

Automated windows open behind the person's applications (``--no-window-focus``,
``gui_launch``; ``agent_execution.rst``). Behind and in front draw at different
rates, so a timed comparison gives both sides the same window state
(``--window-state front`` where they would otherwise differ; ``final_runs``
does so for every two-sided step on macOS). Measured with one development
build, factory startup, the default chat layout, a 1400 x 900 window, an M4 Pro MacBook Pro's 120 Hz display,
macOS 26.3, ``nice -n 19``, three trials of each state interleaved:

=====================================  =================  ===================
Measure                                In front           Behind, covered
=====================================  =================  ===================
Frame drawn (and presented in front)   8.33, 8.34 ms      0.54, 0.57, 0.63 ms
Continuous redraws, interval p50       8.32 ms            0.51-0.52 ms
Animation playback                     24.0 fps           24.0 fps
Window captures                        identical          identical
=====================================  =================  ===================

A frame is the mean of ``redraw_timer`` ``DRAW_WIN_SWAP`` over 120 frames;
captures are ``screen.screenshot`` (Blender's own frame-buffer). In front,
every frame waits for the display (8.3 ms at 120 Hz); a covered window
presents nothing, so it does not. Playback follows the scene's frame rate
either way. One in-front trial was excluded by a rule set before the runs (an
in-front trial counts only if the window was never covered): another
application covered the window mid-run, and with focus a covered window waits
up to a second for a drawable (``nextDrawable``) about once a second, so
playback fell to 3 fps. That is the stall a covered no-focus window would have
if it presented: four runs without the present skip showed 1-second stalls and
3 to 8 fps playback. Start captures were byte-identical across all
six trials; end captures differed only at the cube's edges, by viewport
anti-aliasing samples, in front and behind alike.

Window order was sampled from the window server's on-screen list
(``CGWindowListCopyWindowInfo``: bounds, owner and layer, front to back) every
10 ms in single launches and every 50 ms during GUI suites, and activations
from ``NSWorkspace`` notifications. A raise shorter than the sampling interval
would not be seen. Without focus, no Angelo window was ever sampled above
another application's window: single launches with one to three windows
(Preferences reused and raised, a second main window) and 15 suite processes
over eight GUI suites, every window fully covered by the person's
applications. Each launch's activation (macOS gives it to an application the
front-most one started) was handed back in 23 to 32 ms, once 190 ms while the
app was busy starting. Handling the activation on the next run-loop turn
instead let the window show for one 10 ms sample. Stock Blender 5.2.0's own
``--no-window-focus`` covered the other applications in every sample and kept
the activation until it quit (one launch, 119 of 119 samples). A click on a
covered window to look at it keeps the activation
(``GHOSTNoFocusActivationGuard``); that path is not measured.

Native evaluation and subdivision uploads
=========================================

Whole-graph scheduling and parent-count preparation reject operations without
an update tag before reading their component's visibility. Parent counters are
still reset for every operation, and visible dependencies, evaluation stages,
cycles and update flags keep their semantics. This reduces pointer traversal
during sparse edits without a cache and without changing which operations are
evaluated.

Subdivision's loose positions, normals, paint flags and edge factors use the
existing vertex-buffer update API in contiguous chunks. Temporary CPU storage
is bounded to 64 KiB per extractor, including for large meshes. Face data before
the loose range stays untouched; loose edge endpoints keep their duplicated
ordering, and zero normals and paint flags and unit edge factors are unchanged.
No shading quality, scene limit, Undo boundary or agent verification is reduced.

Replayable checks (isolated factory-startup processes):

* ``test_blender_native_depsgraph.py`` (BACKGROUND) checks evaluated
  parent, constraint and driver results, animated visibility and modifiers,
  collection exclusion, ID deletion and recreation, cycles, metaballs, rig
  deformation and driven shape keys across rebuilds, callback-triggered second
  evaluation, Undo, Redo and save/reload.
* ``test_blender_subdiv_uploads.py`` (GUI) checks solid, wireframe, edit-mode
  and paint shading pixels, plus forced redraws after real mesh edits.
  Face-only meshes also exercise empty loose ranges, custom corner normals and
  enabled paint-selection overlays. Upstream's paint-selection overlay on mixed
  loose geometry is nondeterministic, so paint shading is compared with that
  overlay hidden; loose paint-flag values are verified directly by the native
  buffer tests.
* ``DrawMetalTest.subdiv_loose_*`` (and the corresponding OpenGL and Vulkan draw
  tests when built) reads the actual GPU buffers: non-contiguous source
  vertices, several subdivision resolutions, exact edge endpoints, normals,
  flags and factors, empty meshes and uploads, preserved surrounding values,
  repeated writes and scalar and vector ranges crossing several upload chunks.
  Build with ``WITH_GTESTS``, ``WITH_GPU_DRAW_TESTS`` and
  ``WITH_GPU_BACKEND_TESTS`` enabled.
* ``benchmark_blender_native_depsgraph.py`` measures unchanged, sparse and
  all-object updates at 100, 1,000 and 10,000 objects, recording raw samples,
  wall and CPU time and instruction counters. Run it apart from builds and
  tests, interleave upstream and Angelo binaries built with identical settings,
  and keep their JSON reports under ``ANGELO_QA_DIR``.

Method: identical full-release feature settings with assertions enabled, three
interleaved process pairs, 21 measured CPU updates after three warmups and seven
GPU redraws after three warmups. Medians of process medians, milliseconds:

.. list-table:: Native hot paths
   :header-rows: 1

   * - Workload
     - Upstream
     - Angelo
   * - One transform among 100 objects
     - 0.026
     - 0.014
   * - One transform among 1,000 objects
     - 0.192
     - 0.094
   * - One transform among 10,000 objects
     - 8.754
     - 3.592
   * - Subdivided loose-geometry edit, draw and GPU readback
     - 33.499
     - 4.160

The viewport fixture has 4,097 loose edges, 2,001 loose vertices and one face at
subdivision level two; its solid, wireframe, edit and paint-shading captures
match exactly between builds. Unchanged graph updates stay effectively free.
Dense all-object updates are within run-to-run variation (at 10,000 objects,
47–52 ms on both), so no general dense-update speedup is claimed, and the
complete agent-execution harness shows no substantial change from these native
paths alone.

Native graph construction
=========================

Single-operation components keep their operation in the existing inline vector
during construction. A second operation creates the lookup map and inserts both
operations in their original order; multi-operation components keep the map and
final operation order. Entry and exit inference, update tagging, ownership and
copy-on-evaluation dependencies are preserved, and empty components allocate no
map. These are transient graph structures; saved scenes and the agent's
serialization, staging and Undo policies are unchanged.

The node builder returns an existing ID node before repeating initialization
lookups. Previous-build visibility, evaluation flags and custom-data masks are
still restored on the ID's first creation in each build, and no ID node is
kept across a rebuild by this shortcut.

``DegComponentNodeTest`` checks empty, single and multiple operations, lookup
keys, callbacks, map iteration order, inferred and explicit endpoints, tagging
and cleanup. ``DegBuilderNodesTest`` checks repeated ID requests and metadata
restoration across rebuilds, and the native evaluation fixture checks rigged
meshes with driven shape keys before and after graph reconstruction.

``benchmark_blender_native_graph_build.py`` links and unlinks one object in
scenes of distinct quad meshes; every sample includes two graph rebuilds, their
evaluations and collection synchronization. Three interleaved process pairs,
two warmups and nine measured samples each, medians of process medians:

.. list-table:: Graph construction, milliseconds
   :header-rows: 1

   * - Objects
     - Upstream
     - Angelo
   * - 100
     - 2.118
     - 2.058
   * - 1,000
     - 38.273
     - 30.064
   * - 5,000
     - 225.494
     - 202.419

Instruction medians are 4.9–6.6% lower in these graph workloads. The complete
agent-execution harness changes far less (a staged single action in the
2,000-object cohort, 108 against 103 ms, with overlapping ranges), so local graph
gains are not extrapolated to model-driven jobs.

Native reads against stock Blender
==================================

``bench_native_reads.py`` times every native read Angelo adds that stock
Blender can also do, against every stock Python route found for it, on the
dense scene of ``scenario-heavy`` (1,969 objects, 1,247 meshes, 2,662,867
vertices, built from its seed when no ``--scene`` is given). Every route of a
read must first return the same data (``parity``); each is then timed seven
times after a warm-up, after a garbage collection with the collector off, on
the main thread (wall time, and on macOS retired instructions; Windows reports
those as null). The comparison is the fastest *supported* stock route; an
unsupported raw-memory copy is listed beside it. Each route looks its reader
up once, as the stock routes look up their collections once
(``vertices = mesh.vertices``), and ``blend_listing`` runs with the product's
own limits and deadline. To run it (macOS or Windows; with a stock Blender
only the stock routes run)::

    BLENDER -b --factory-startup --python tests/python/angelo_agent/bench_native_reads.py -- --out DIR --web

The report names the timed binary by its own build, read inside it
(``build_hash`` and ``build_branch`` from ``bpy.app``; ``angelo_commit`` is
that hash for an Angelo build), and the harness checkout apart
(``harness_commit``). A binary that reports no build hash is refused before
anything is timed.

Measured on an Apple M4 Pro (14 cores) with the machine otherwise quiet (a
1-minute load average of about 2 for the whole round), on a signed release
build of Angelo and on stock Blender 5.2.0: the same saved scene, the binaries
one after another, medians of seven runs each. *Stock* is the stock route
timed inside Angelo, *stock binary* the same route in stock Blender:

.. list-table:: Native reads (milliseconds; per million vertices for the per-vertex reads)
   :header-rows: 1

   * - Read
     - Fastest stock route
     - Stock
     - Stock binary
     - Angelo
   * - Vertex positions
     - ``attributes["position"].data.foreach_get("vector")``
     - 0.627
     - 0.610
     - 0.365
   * - Vertex-group weights
     - bmesh deform layer
     - 352
     - 353
     - 1.27
   * - Face corners and offsets
     - ``.corner_vert`` attribute and ``loop_start``
     - 21.8
     - 21.3
     - 0.431
   * - A ray-cast tree of the terrain
     - ``BVHTree.FromObject``
     - 56.6
     - 58.8
     - 57.1
   * - 1,000 single-vertex group reads
     - ``vertices[i].groups``
     - 0.718
     - 0.736
     - 0.557
   * - 1,000 image size and format reads
     - ``Image.size`` and friends
     - 0.343
     - 0.335
     - 0.227
   * - List a 250 MB .blend's objects
     - ``bpy.data.libraries.load``
     - 117
     - 116
     - 37.8
   * - The mesh being sculpted
     - ``evaluated_get().to_mesh()``
     - 0.013
     - 0.010
     - 0.010
   * - 1,000 ray hits while sculpting
     - ``Object.ray_cast``
     - 1.73
     - 1.63
     - 23.5
   * - 1,000 vertex positions while sculpting
     - ``vertices[i].co``
     - 0.979
     - 0.946
     - 0.909

Against the stock route timed inside Angelo (the ratio is Angelo's median over
that route's, so below 1 is faster), Angelo takes 0.58 times as long to read
vertex positions, 0.004 for vertex-group weights (about 280 times faster than
the bmesh deform layer), 0.02 for face corners and offsets (about 50 times
faster), 0.78 for single-vertex group reads, 0.66 for image size and format and
0.32 for listing a .blend; against stock Blender's own binary the same six
ratios are 0.60, 0.004, 0.02, 0.76, 0.68 and 0.32.

Three reads are level with stock, not faster, and are not claimed as speed-ups.
The ray-cast tree is 1.01 times stock's (57.1 against 56.6 ms; 825 against 827
million instructions): it builds the same tree. The mesh being sculpted takes
10 microseconds either way (0.010 ms, against 0.013 for the stock route inside
Angelo and 0.010 in stock Blender, with overlapping ranges). Sculpt vertices
are 0.93 times the stock route inside Angelo (0.909 against 0.979 ms, 7% fewer
instructions) and 0.96 times stock Blender's (0.946 ms), about the 3 to 4% by
which the same stock route differs between the two binaries in this round
(``BVHTree.FromObject``, 56.6 ms inside Angelo against 58.8 in stock Blender).
Sculpt rays are the one read slower than stock: 23.5 ms for a thousand rays
against 1.73 for ``Object.ray_cast`` inside Angelo (13.6 times) and 1.63 in
stock Blender (14.4 times; 382 million instructions, inside the ratchet ceiling
of 383 million). It is not the same work: Angelo reads the live sculpt surface
(hidden faces, Multires grids), ``ray_cast`` the evaluated mesh.

Each of these reads exists for something stock Blender's route does not give,
so a slow read is made cheaper on the native path, never switched to the stock
route:

* **Vertex positions** (``Mesh.angelo_sculpt_positions``; also the topology and
  weight buffers): owned bytes for the exact revision and fingerprint, never a
  native pointer lent to Python. The getter writes straight into the bytes
  object (``RNA_property_string_get_direct``) instead of pyrna's three copies
  (a zero-filled ``std::string``, a ``MEM`` buffer, the bytes object).
* **Image size and format** (``_angelo_read.image_metadata``): from the image
  cache only, never loading, generating or reading back an image and never
  writing its last frame or last-used time, as the stock getters do. Most of
  its time was building its record, every key and word a new string; records
  are built from interned words kept in the module.
* **Listing another .blend** (``metadata_scan``): descriptor-only, bounded,
  cancellable, with no readfile, versioning or Main code and no Python import
  handlers. A system call per block header (Blender writes one block per
  weighted vertex) and a clock read per budget charge were 81% and 16% of its
  profile, so plain files are read in 64 KiB windows and the clock is read
  every 32nd poll. The block limit is 32,000,000 headers over all passes, about
  half a second of header work at this cost, so this 250 MB scene is listed
  instead of refused (``scene_intelligence.rst``).
* **Sculpt rays** (``Object.angelo_sculpt_query``): the live sculpt surface
  (Multires grids, deform positions, hidden faces, working-surface indices)
  without re-evaluating; ``Object.ray_cast`` reads the evaluated mesh, which a
  stroke does not update, which is the Multires cage in Sculpt Mode and which
  includes hidden faces, so the two do not do the same work. 95% of the query's
  time is testing every triangle of 2,500-face leaves. Per-leaf ray indices
  answered a thousand rays in 1.80 ms against 1.89 for ``ray_cast``, but they
  are not used until they answer every ray exactly as the scan does
  (``native_sculpting.rst``).
* **Sculpt vertices** (``Object.angelo_sculpt_vertex``) and **the mesh being
  sculpted**: the same live surface. The vertex read looked the dependency graph
  up through the context on every call, and Blender found an RNA function by
  comparing its name with each of the type's functions in turn (this one is
  39th of ``Object``'s 52); the mesh read woke the task scheduler for a normal
  update with nothing to update. All three are gone from the common case.

Registered properties are read through the address-read ABI
(``angelo_read_data``), which reads stored members without running a ``get=``,
transform or item callback and without creating or repairing anything. A
separate reader that returned a record (``state``, ``type``, ``value``) for each
property cost more than stock's whole read, which returns a cached small integer
and allocates nothing (1,000 reads: 0.116 against 0.055 ms with interned
words), and was removed; ``bench_native_reads_paths.RETIRED`` keeps its numbers
and ``native-reads.json`` lists them under ``retired``.

Still slower, and why: sculpt rays (above), until the faster indices are proven
exact, and listing a *compressed* .blend, which is not one of the reads above.
The castle saved with Compress (71 MB of Zstandard) takes about
0.8 s against 0.3 s for ``libraries.load`` under load 20. The scan decodes the whole stream on each of
its two passes; stock decodes once and skips whole frames through the file's
seek table, which the scan deliberately does not trust. Matching it needs one
pass and validated frame skipping, a change to untrusted-file handling.

The safety check of one edit (the edited object's exact revision and program
fingerprint, then the whole-document check that nothing else changed) takes
68 ms after a normals edit on a 77,220-vertex wall and 328 ms after halving a
705,538-vertex statue, against 151 ms and 1,289 ms for the same reads through the
fastest stock routes (2.2 and 3.9 times as long), measured in the same round.
The document check is 35 ms in both: it already uses stock Blender's fastest
routes.

The instruction ratchet holds each of these reads (``native_reads/<read>``,
`Instruction ratchet as a merge gate`_).

``--web`` also writes ``native-reads.json`` (every read above, with every
stock route) and a point cloud of the scene for the read animation
(``castle-points.bin``: 296,997 points, every ninth vertex, little-endian
float32).

Scene queries
=============

Scene-query scaling
-------------------

``Object.users_collection`` rescans every collection's object list on each
call, so whole-scene passes that read it per object were quadratic in object
count. Query passes use one bounded membership index instead, revision digests
use BLAKE2b, and finite-value and flag scans run in C (``scene_intelligence.rst``).
In single-change ablations the membership index alone cut search time to 0.41×
at 2,000 objects and 0.16× in the 511-collection tree, with no change at 100
objects; BLAKE2b alone gave 0.70–0.82× on revisions and queries at 100 objects
and on dense meshes.

BLAKE2b keeps hashing cost predictable across builds. Angelo's bundled Python
3.13 (OpenSSL 3.5) hashes 1 MiB with SHA-256 at about 129 MB/s and with BLAKE2b
at about 1.5 GB/s; with OpenSSL 3.6, SHA-256 reaches about 3.1 GB/s. NumPy scans
would save a further 2–4% on staged programs, but scene modules stay importable
by the dependency-free host tests, whose interpreter has no NumPy.

Object-scoped observations
--------------------------

Object detail and relationship queries mint object-scoped observation tokens
instead of fingerprinting the whole scene, and ``coverage="PAGE"`` search
fingerprints only its returned page (``scene_intelligence.rst``). With
``benchmark_blender_agent_execution.py`` (three interleaved runs, one warmup
plus five samples each, medians of run medians), a search of limit 10 at 2,000
objects took 239–267 ms of wall time with ``SCENE`` coverage and 54–57 ms with
``PAGE``, and a rich object query 77 ms instead of about 300 ms. A page saves
nothing when it is a large share of the scene; a page of 10 in the dense cohort
is half of it.

Revisions and fingerprints read the native vertex-group buffer
(``Mesh.angelo_vertex_group_weights``) instead of walking each vertex's
``groups``: Blender's Python API has no bulk vertex-group read, and the walk
alone took 7.4 ms for 16k vertices even with no groups, and 119 ms per revision
on a 20,164-vertex grid with four weights per vertex.

Mesh attribute reads
--------------------

Exact revisions and program fingerprints copy mesh data from its contiguous
attributes instead of reading per-element RNA: on a 28,900-vertex grid,
positions take about 0.01 ms against 0.91 ms through RNA. With the native
vertex-group buffer, an exact revision of that grid takes 18–21 ms of process
CPU and its program dependency fingerprint 24–26 ms (two interleaved runs,
medians of seven calls).

Large-mesh budgets
------------------

Revision and fingerprint work budgets charge packed whole-array copies by
bytes, against budgets separate from per-element work
(``scene_intelligence.rst``, ``agent_execution.rst``). Charging C-speed copies as
per-element work made staged programs fail closed from about 40,000 vertices,
well under the documented 100,000-vertex input limit. Process CPU time on plain
grids:

.. list-table:: Large meshes
   :header-rows: 1

   * - Vertices
     - Exact revision
     - Program fingerprint
     - Staged transform program (wall)
   * - 40,000
     - 13 ms
     - 14 ms
     - 408 ms
   * - 99,856
     - —
     - —
     - 501 ms
   * - 250,000
     - 72 ms
     - 73 ms
     - refused by the 100,000-vertex input limit

Programs on a mesh above the input limit stop at that limit with an actionable
error.

Shared reads within a pass
--------------------------

Within one read pass, a material's node graph and tree revision are read once
and replayed for every later object that uses it (``scene_shared_reads``);
budgets are charged exactly as if each object read them, so budget exhaustion
and limitations do not move. ``generic_settings.writable_paths`` caches the
static RNA walk per node type, the limitation and dependency-ID walks keep an
explicit stack instead of a generator or call per nested container, and
``scene_read_values.dump`` reuses one ``JSONEncoder``. On the 2,000-object
search of 100 these steps took the pass from about 3,160 to 2,030 million
instructions (main-thread CPU about 178 to 110 ms).

The compiled-app proof compares 72 query results (searches, pages, details,
relationships, revisions and deltas on every benchmark cohort, a scene mixing
shared, distinct and no materials, and three runs whose metadata budget runs
out early) with epochs, tokens and memory addresses made deterministic: every
result is byte-identical with and without shared reads, and no model-visible
byte changes.

Request host work on a production-size scene
--------------------------------------------

``benchmark_blender_request_host_time.py`` builds 5,000 props in 64 collections
sharing 40 node materials plus eight 99,856-vertex meshes (5,012 objects,
0.84 M vertices) and measures one request's host work on the main thread (two
runs):

.. list-table:: One request's host work
   :header-rows: 1

   * - Step
     - Instructions
     - CPU
   * - Turn context
     - 498–516 M
     - 26 ms
   * - Whole-scene search of 100
     - 6,812–6,821 M
     - 359–366 ms
   * - Discovery page of 10
     - 380 M
     - 21–24 ms
   * - Object detail
     - 728–729 M
     - 42–43 ms
   * - One-operation staged edit
     - 3,064–3,075 M
     - 265–269 ms
   * - Request snapshot
     - 350 M
     - 36–40 ms
   * - Total
     -
     - 752–765 ms

Where the time goes: the whole-scene search exhausts its 12,000-item metadata
budget after about 100 objects, and the other 4,900 partial captures take most
of its time; the staged edit spends most of its time in native commit and
stage-bundle I/O; and each post-commit ``live_revisions`` entry (one exact
revision per written object, which spares the model a lookup round per edit)
costs about 60 million instructions, 3.7–3.9 ms of CPU.

Script executor overhead
------------------------

Around a reviewed script's own edit, ``BlenderExecutor.execute`` validates the
call, signs the scene before and after (``scene_signature``), pushes the
restore point and reads the script's ``after`` (``script_after.attach``). Three
general cuts, each keeping every fact the result and the safety checks read:

* **One analysis per text** (``script_source``): the compile and the
  removed-name walk are pure functions of the text, kept for the last 8 texts.
  A worker call validated the same text six times (admission, preconditions,
  execute) and compiled it once more to run.
* **One matrix read per signature**: every world matrix in one
  ``foreach_get``, kept as bytes that compare equal exactly when every value is
  equal to 6 decimals, as the rounded lists did.
* **One pass per read-back** (``revision_pass``, ``script_after.named``): one
  scene listing and one shared-read memo for the rows' revisions (each row's
  own budget, so each revision is ``object_revision``'s), and one name index
  for the rows, the live Booleans and the motion, where each
  ``scene.objects.get`` walked the scene.

Measured in the worker Blender of the concurrent-edit fixture (64-object
script batches, up to 1,536 objects per side, shipped settings), one dev build
of 310a6eaabfd with each tree's add-on, three interleaved runs per side, medians
per call in ms of wall time on a shared, loaded Mac:

.. list-table:: A worker's script call
   :header-rows: 1

   * - Step
     - 310a6eaabfd
     - With the three cuts
   * - ``execute``
     - 61.7
     - 44.5
   * - ``execute`` outside the script's run
     - 29.2
     - 18.2
   * - ``attach``
     - 18.0
     - 12.5
   * - ``scene_signature`` (two per call)
     - 7.05
     - 4.16
   * - Validation (six per call)
     - 2.04
     - 0.02
   * - Compile to run
     - 1.94
     - 0.00
   * - One row's revision
     - 0.47
     - 0.32
   * - Motion and live Booleans
     - 3.6
     - 0.4
   * - ``precondition`` (four per call)
     - 3.39
     - 1.51

Left as they are: the restore point's Undo push (4.6 ms, the file a raise
rolls back to), the scene settings' revision (2.2 ms, never left out), and the
rest of each signature (about 3 ms at 1,000 objects: vertex digests, material
slots, modifiers, motion), which no cheap invalidation key covers.

Per-edit cost over a long session
=================================

The 40-task long benchmark's receipts show an edit's
``verification_ms + commit_ms`` rising about 3.2 ms per edit, from 6 ms on the
first edit to 107 ms by edit 32, with ``precondition_ms`` and ``staging_ms``
rising alongside. The session's length does not cause it. Every fifth task of
that set gives the same cube a new Principled material in a new slot (the model
asks for a new material; ``material_principled`` appends a slot), and every
exact check of an edit reads every material of the object it edits. Per-edit
host cost is linear in the edited object's dependencies and nearly flat in the
session's length; the growth with session length that existed was outside the
receipt's check phases, and is flat or much smaller (`Session-length costs
outside the checks`_). The measurements up to `What changed`_ are of the code
before the change that section describes, which made an edit of a 32-material
object about 3.6 times cheaper in the app.

Receipts of a recorded run of that set before the change, median of five Opus
trials (ms; materials are the cube's at the time of the edit):

.. list-table:: Long session, per edit, before the change
   :header-rows: 1

   * - Edit
     - Materials
     - Precondition
     - Staging
     - Verification
     - Commit
     - Verification + commit
   * - 2
     - 0
     - 8.6
     - 3.7
     - 1.3
     - 5.4
     - 6.7
   * - 6
     - 1
     - 27.9
     - 11.6
     - 4.8
     - 17.3
     - 22.1
   * - 14
     - 3
     - 63.8
     - 30.1
     - 11.4
     - 39.5
     - 50.9
   * - 22
     - 5
     - 98.8
     - 45.5
     - 17.5
     - 61.1
     - 78.6
   * - 32
     - 7
     - 136.5
     - 62.0
     - 23.9
     - 82.7
     - 106.6

The cost rises in steps after each material edit and barely within a cycle.
Across 155 recorded edits, precondition + staging + commit fits 24 ms + 37 ms
per material (R² 0.996); in this task set the material count and the edit
index rise together, so the receipts alone cannot separate them.

Unbilled replay
---------------

``long_session_replay.py`` separates them. It runs the long set's cycle (move,
resize, bevel, material, question) in one chat through the unchanged live-eval
driver (real controller, UI pump, default approval, commit operators, request
checkpoints, receipts and host grading) with a scripted runtime in place of the
provider, answering each prompt with the typed program Opus sent for it, bound
to the previous edit's post-commit revision. ``append`` gives the cube a new
slot per material as Opus did; ``replace`` puts each new material in slot 0, so
the cube keeps one material while the file gains an orphan material and mesh
per cycle. Precondition + staging + commit per edit (ms), 627 tasks (500
committed edits in ``replace``), GUI app under a 1-minute load of 9–14:

.. list-table:: Replay, host phases per edit
   :header-rows: 1

   * - Edit
     - ``append`` (cube materials)
     - ``replace`` (cube keeps 1)
   * - 2
     - 20.6 (0)
     - 19.4
   * - 50
     - 505.9 (12)
     - 60.9
   * - 130
     - 1,339.4 (32, the slot limit)
     - 64.0
   * - 200
     - 1,369.0 (32)
     - 68.4
   * - 400
     - 1,284.0 (32)
     - 69.3
   * - 500
     - (408 edits committed)
     - 73.6

At edit 32 the ``append`` replay's verification + commit is 121.8 ms against
the recorded 106.6. With the cube's materials growing, the cost grows; once the
32-slot limit refuses further materials, it stays at 1.28–1.40 s per edit (median 1.32 s) for the remaining 279 edits.
With the materials held constant it stays at 59–85 ms per edit over 500 edits
(61 ms at edit 50, 74 ms at edit 500). Extrapolating the benchmark's slope to a
500-edit session (1.6 s per edit) therefore does not hold: the cost follows the
object being edited. An object with 32 Principled materials costs about 1.3 s
of host work per edit at any point of a session.

Where a material's cost went
----------------------------

An edit ran nine full exact checks of each written object: six binds (the
admission precondition, four fresh preconditions at the controller's fences and
the job's own bind) and three verifies (after staging, in ``ProgramJob.commit``
and in the commit itself); the four fresh preconditions run before staging, at
native preparation, immediately before the typed apply and before commit. Each
check captures the object revision (``scene_fingerprints``), hashes the
complete RNA of every mutable dependency
(``program_dependencies._fingerprint``) and walks the dependency closure
(``_mutable_dependencies``, 27 walks per edit counting stage validation and
admission). Staging also copied every material and node tree into the private
stage. All of this was proportional to the object's materials and node trees,
whatever the program wrote; a transform edit paid for 32 materials it never
touched.

``benchmark_blender_long_session.py`` measures the same move through
``native_programs`` directly (bind, stage, checks, commit; none of the
controller's fences), main-thread instructions, four runs within 0.5%:

.. list-table:: One-operation move by the cube's material slots
   :header-rows: 1

   * - Slots
     - Instructions
     - Wall (under load)
   * - 1
     - 397 M
     - 26 ms
   * - 8
     - 2,464 M
     - 153 ms
   * - 32
     - 9,479 M
     - 555 ms

That is about 292 million instructions (17 ms) per Principled material in the
native path; the app's extra fences made it 37–41 ms per material in the
receipts and the replay. In a 32-slot move, cProfile attributed about 40% to
the dependency link walks (``_embedded_links``), 27% to the RNA fingerprints,
13% to revision captures and 11% to custom-link admission.

What changed
------------

Three changes (``agent_execution.rst``, "Materials a program cannot write" and
"Exact fences"), every check still comparing the same exact content:

* **Stage only what a program can write.** An APPLY program of
  material-blind kinds (a move, a typed modifier edit) reads each eligible
  live material in place instead of copying it with its node tree into the
  stage (``stage_borrowing``). A 32-material move copies two IDs instead of
  66, and stage validation no longer walks 64 clones. A PREVIEW, and any edit
  of an object whose closure has an Action, NLA tracks or drivers, still
  copies every material: the stage's active depsgraph writes animation back
  through a staged slot, so a borrowed material could be written live.
* **One read per fence.** Inside one read-only span (``exact_fence``) each
  datablock's links, each object's closure, fingerprint, custom-link admission
  and revision are read once: ``bind`` walks each closure once instead of
  three times, the commit operator's fresh precondition serves the job's first
  verification, and commit's preparation fence serves its final verification
  and stage validation. Commit now evaluates the stage before those checks
  rather than between them and the first live write.
* **Each type's declarations once.** The exact walkers read every value of
  every node and socket, but most of their time went to reading the same few
  dozen RNA types' property declarations again at each one. They now read each
  type's declarations once per fence, and charge and check plain values
  without a recursive call.

Main-thread instructions of the 32-slot move after each change, one run of
the benchmark's move with per-function counters (the benchmark's medians
below differ by up to 2%):

.. list-table:: The 32-slot move, change by change
   :header-rows: 1

   * - Code
     - Instructions
   * - Before
     - 9,442 M
   * - Borrowed materials
     - 8,453 M
   * - Exact fences
     - 7,211 M
   * - Declaration tables
     - 3,702 M
   * - Plain values without a call
     - 3,270 M
   * - No borrowing near animation; eligibility re-walked at hydration
     - 3,363 M

``benchmark_blender_long_session.py``, base and change back to back on the
same binary (1-minute load 6–8; wall clock is ``nice -n 10``), with the
animation rule below:

.. list-table:: Before and after, ``benchmark_blender_long_session.py``
   :header-rows: 1

   * - Operation
     - Before
     - After
     - Ratio
   * - Move, 1 slot
     - 396 M, 25.9 ms
     - 230 M, 15.7 ms
     - 1.72×
   * - Move, 8 slots
     - 2,457 M, 139.9 ms
     - 901 M, 55.3 ms
     - 2.73×
   * - Move, 32 slots
     - 9,446 M, 533.6 ms
     - 3,353 M, 212.9 ms
     - 2.82×
   * - Edit cycle, first ten (median)
     - 1,669 M
     - 1,012 M
     - 1.65×
   * - Edit cycle, last ten of 500 (median)
     - 1,690 M
     - 1,029 M
     - 1.64×

A program borrows nothing when its objects' closures are animated: an F-Curve
of the moved object whose path runs through a slot into the material would be
written into the borrowed live material by the stage's first evaluation.
Hydration walks each borrowed material's family again, 138 M (4%) of the
32-slot move (two hydrations of 32 families). The benchmark's objects are not
animated, so they borrow.

The closure holds every object data type (Grease Pencil, hair curves, curves
and text included), an ID a collection holds is a link, and the link walks
skip per-element views. Collections of IDs are classified once per RNA type
(``rna_fields.link_fields``) rather than by an ``isinstance`` test of every
collection child, and ``_walk_links`` uses no generator expression, which
would make two of its locals closure cells for every field it reads (the two
together cost the 32-slot move 3%). Non-mesh objects with one material, one
move, main-thread instructions (median of five; wall clock under load):

.. list-table:: A move of a non-mesh object
   :header-rows: 1

   * - Object
     - Instructions, wall
   * - Hair curves, 5,000 curves of 16 points
     - 2,966 M, 150 ms
   * - Grease Pencil, 5,000 strokes of 16 points
     - 3,099 M, 158 ms
   * - Curve, 64 Bézier points
     - 387 M
   * - Text
     - 280 M

Walking every attribute value, point and stroke offset in each link walk and
each custom-link admission cost the hair move about 70 billion instructions
(3.6 s) and the Grease Pencil move about 27 billion (1.4 s). Those views' item
types declare no pointer or collection (``test_blender_borrowed_materials_closure``
proves it on the binary), so the walks skip them.

The app gains more, because its commit runs more of the checks that a fence
now shares. The ``append`` replay, base and change (with the animation rule)
back to back on the same binary (300 tasks, 213 committed edits, every one
graded passed on both; GUI, 1-minute load 7–14), precondition + staging +
commit per edit, ms (verification + commit in brackets):

.. list-table:: Replay, ``append``, host phases per edit
   :header-rows: 1

   * - Edit (cube materials)
     - Before
     - After
     - Ratio
   * - 2 (0)
     - 19.7 (7.5)
     - 16.6 (5.4)
     - 1.2×
   * - 50 (12)
     - 541.4 (206.3)
     - 198.3 (67.1)
     - 2.7×
   * - 200 (32)
     - 1,320.0 (501.9)
     - 365.5 (112.0)
     - 3.6×
   * - Median of the 85 edits at the slot limit
     - 1,302.7
     - 366.5
     - 3.6×

At the slot limit commit's hydration re-walk adds about 5 ms (64.3 → 69.0 ms
median commit).

What is left scales with the materials as before, with a smaller constant:
about 100 million instructions (5–6 ms) per Principled material in the
benchmark's move, 10–11 ms in the app. Of the 32-slot move's 3,363 million
instructions, the four RNA fingerprints are 45%, the six object revisions
(``scene_fingerprints``, unchanged) 21%, the four custom-link admissions 15%,
the stage validations' and commit's closure walks 13% and hydration's
eligibility re-walks 4%. Commit's own hydration follows its verification
against the binding, which already covers every borrowed family; sharing one
fence between them, before hydration, would make that re-walk nearly free.

Session-length costs outside the checks
---------------------------------------

The ``replace`` replay with ``ANGELO_REPLAY_HOST_TIMERS=1``
(``long_session_timers.py``: wall time and the calling thread's retired
instructions inside the session-length functions) found three costs that grew
with the session whatever the edit: the conversation journal's main-thread
saves (about four per edit), the admission save a provider thread makes under
the controller lock, and the snapshot worker's per-capture maintenance (its
whole-history inventory, store accounting and garbage collection), which
shares the GIL with the main thread. Each save rewrote the whole journal (3.6
MB after 627 tasks, ``json.dumps`` 16 ms per save) and revalidated the whole
mutation-ID ledger; the worker read, validated and fingerprinted every stored
snapshot twice per capture and recounted every manifest twice.

They are now flat or much smaller:

* **The journal appends** (``agent_execution.rst``, "The conversation
  journal"): a save encodes the current request's rows and what changed, and
  appends and syncs one record, with the same durability; ledger entries are
  validated when they are added; the cleaned task memory is reused until the
  memory changes. The file is rewritten whole at chat close, after a load and
  once its records outgrow the base (11 times over this replay), and compacted
  at a switch to another chat only when it holds records or a change (a switch
  with nothing new writes nothing: 0.17 ms against 14.6 ms on a 2.5 MB chat).
* **The worker's probe re-reads only changed records** and serves both of its
  probes with one scan, plans from an upper bound of the store's bytes before
  reading every manifest, collects garbage only after a dropped manifest, and
  its probes and accounting pause for the main thread about every half
  millisecond, never while holding a lock another thread waits for
  (``checkpoint_policy.rst``, "The worker's per-capture probes").

Median per edit of the ten edits ending at edits 10, 50, 200 and 500 (the
medians of four runs before and two after; GUI app, 1-minute load average
6–11):

.. list-table:: Session-length costs per edit, before → after
   :header-rows: 1

   * - Work
     - 10
     - 50
     - 200
     - 500
     - 500 / 50
   * - Journal, main-thread saves (ms)
     - 12.0 → 3.6
     - 26.9 → 6.2
     - 60.8 → 7.2
     - 106.0 → 7.2
     - 3.93 → 1.17
   * - Journal, main-thread saves (M instructions)
     - 159 → 42
     - 413 → 72
     - 923 → 73
     - 1,611 → 73
     - 3.90 → 1.02
   * - Admission save on the provider thread (ms)
     - 2.8 → 0.6
     - 6.6 → 0.8
     - 15.0 → 0.7
     - 26.5 → 0.8
     - 4.02 → 1.01
   * - Admission save (M instructions)
     - 38.0 → 3.7
     - 102.8 → 4.1
     - 230.4 → 4.2
     - 403.0 → 4.4
     - 3.92 → 1.07
   * - Whole-ledger validation inside saves (ms)
     - 0.3 → 0
     - 1.0 → 0
     - 3.8 → 0
     - 9.4 → 0
     -
   * - Snapshot worker's inventories (M instructions)
     - 39 → 14
     - 153 → 19
     - 553 → 30
     - 821 → 54
     - 5.38 → 2.90
   * - Snapshot worker's whole maintenance (M instructions)
     - 71 → 38
     - 209 → 44
     - 730 → 63
     - 1,975 → 102
     - 9.47 → 2.33
   * - ``main_thread_block_ms``
     - 43.3 → 40.3
     - 53.0 → 45.8
     - 69.6 → 45.9
     - 92.7 → 46.9
     - 1.75 → 1.02
   * - Replay ``total_ms``
     - 178.6 → 174.1
     - 192.7 → 181.1
     - 219.0 → 181.6
     - 253.5 → 182.6
     - 1.32 → 1.01

The journal's growth from edit 10 to 50 is the task memory filling its bound
(24 verified results, cleaned once per edit), then it is flat. Its wall time
still rises about 1 ms per edit by edit 200 while its instructions do not: the
saves' system calls hand the GIL to the snapshot worker, which still works in
proportion to the history. The worker's probe calls ``lstat`` for every stored
snapshot to stay exact (``checkpoint_policy.rst``), so it stays linear, at
about a twentieth of its old per-record cost; store accounting is read only
when the upper bound does not settle the byte limit. The exact checks are
unchanged (about 46–50 ms per edit). Commits that swap mesh data leave the
replaced mesh as an orphan (124 orphan meshes and 124 orphan materials after
500 edits) until the file is saved and reopened; that has no measurable effect
on the checks.

What would make the checks flat
-------------------------------

Fewer full checks per edit, staging only what a program can write and a
cheaper walk are implemented (`What changed`_), and so is a journal that
appends (`Session-length costs outside the checks`_). Each item below changes
an exact safety check or staging, so each must first be proven to keep it
exact.

* **A native exact walk.** The same complete content (RNA values, links,
  custom-link admission) hashed in C++ instead of Python attribute reads;
  linear, with a much smaller constant (not measured). The fingerprint (its
  closure, RNA walk and JSON encoding) is now about 60% of a check.
* **The object revision of a material.** ``scene_fingerprints`` reads every
  node and socket of each material (about 3.6 million instructions each) in
  each of an edit's six revisions; no fence can share revisions across the
  edit's yields.
* **More material-blind kinds.** Object-level kinds (rename, visibility,
  collections, parenting, constraints) could borrow too; each needs its kernel
  and commit transfer proved material-blind first.
* **O(changed) checks** need per-ID change tracking, which the exactness
  contract excludes today (fresh content, never "pointer, count or
  depsgraph-only caches", ``agent_execution.rst``).

Measured and not adopted: one membership index of the bound scene per fence.
Each object revision tests ``obj in scene.objects[:]`` and reads
``Object.users_collection``, which scan the scene's objects and every
collection: 2.1 million instructions of a 16-million revision of a one-cube
prop among 2,000 objects in 511 collections. A Python index of the whole scene
(``collection_memberships``, exactly ``users_collection``'s membership and
order) costs 14–19 million to build, so a fence that reads revisions only
breaks even at about ten objects: five revisions cost 79 → 98 million with
it, 25 cost 392 → 340 million, and a turn context (at most nine revisions)
cost 3–8% more. Within a fence each object's revision is already read once,
and turn contexts, post-commit revisions and typical programs read fewer than
ten.

``benchmark_blender_long_session.py`` is part of the instruction ratchet:
``long_session/edit_cycle_first_10`` and ``long_session/edit_cycle_last_10``
(the median of the first and last ten move-resize-bevel-material cycles of a
500-edit session; the run fails when the last exceeds the first by more than
10%) keep per-edit cost from growing with the session, and
``long_session/move_material_slots_{01,08,32}`` pin the per-material slope so a
fix above can lower it and nothing can steepen it unnoticed; their ceilings
were lowered with the change above.
``benchmark_blender_session_journal.py`` saves 500 edits through the product
``SessionStore`` exactly as the controller does (request row, admission,
staging, commit start, completion with a new task memory, turn end) and ratchets
``conversation_journal/edit_saves_at_{50,500}`` and
``conversation_journal/admission_save_at_{50,500}`` (the median of the ten
edits ending there; the run fails when edit 500's exceed edit 50's by more than
10%; measured 4.4% and 0.9–2.8%: the results table filling to its 256 records
and wider row numbers), and ``snapshot_worker/probe_unchanged_{0100,1000}``,
the worker's probe of an unchanged history of 100 and 1,000 snapshots (13.7
and 127.7 million instructions: linear, pinned per record). The replay is
manual (GUI, about 3–5 minutes for 627 tasks in ``replace`` mode; usage in its
docstring).

Staged programs
===============

Stage cleanup scans
-------------------

A staged program's teardown maps Main once and verifies once; the ownership
check reads identities only for captured pointers; blocks are removed from
their own ID collection; a clean commit teardown is not re-scanned; and
``assert_staged`` uses the single-user rule (``agent_execution.rst``). A
one-operation program in a 2,000-object scene scans Main 18 times and reads
``Object.users_collection`` 5 times, about 300 ms of process CPU per program
(80 ms at 100 objects).

What remains at 2,000 objects is mostly Blender itself: per-ID removal remaps
usages across the whole file (about 7 ms to remove five stage IDs one by one,
3 ms in one batch call), and creating or removing any Main ID makes the artist
scene's dependency graph rebuild its relations. In that scene a
``view_layer.update()`` after a transform change took a median 17 ms, but
337 ms after a stage-like scene, collection, object and mesh had been created
and removed. Every stage hydration and suspension churns IDs this way, which is
why polls are fused.

Fused staging polls
-------------------

A native poll hydrates the private stage once and fuses units: preparation,
operation quanta, every expected check, stage validation and read-back run in
one poll while the poll's work stays under 16 ms (``agent_execution.rst``).
Hydration and serialization do not count against that budget, and every poll
completes at least one unit. Re-verifying a preview's exact revisions does not
hydrate the stage by itself, and neither does a poll that is already ready to
commit. Commit re-hydrates, re-runs every check and verifies exact revisions
before writing.

Process CPU per program, medians of three interleaved runs, one warmup plus
five programs each; run-median ranges in brackets:

.. list-table:: Fused polls, process CPU per program
   :header-rows: 1

   * - Program
     - Polls
     - Poll CPU, ms
     - Longest poll, ms
     - With commit, ms
   * - 2,000 cubes, 1 action, 1 check
     - 1
     - 17.0
     - 17.0
     - 110.6 [105–112]
   * - 2,000 cubes, 8 actions, 3 checks
     - 1
     - 21.0
     - 21.0
     - 117.6 [115–122]
   * - 2,000 cubes, 24 actions, 6 checks
     - 1
     - 27.8
     - 27.8
     - 129.4 [118–134]
   * - 28,900-vertex grid, 1 action, 1 check
     - 2
     - 261.9
     - 139.3
     - 497.6 [453–527]
   * - 28,900-vertex grid, 24 actions, 6 checks
     - 4
     - 360.9
     - 140.3
     - 611.7 [485–713]

Before fusing, the same programs took 4, 6, 11, 4 and 15 polls. On the grid,
preparation alone (the input's exact revision and dependency fingerprint)
exceeds the budget and keeps its own poll; the longest poll is read-back's
exact verification of that mesh.

In the GUI (2,000 cubes, one action and one check; each simulated UI tick
polls, updates the artist view layer as the event loop's dependency-graph pass
does, then redraws the 3D view; commit called directly, without an Undo push),
a program takes about 231 ms of wall time, of which about 56 ms is the one
view-layer update after its poll; unfused, four polls cost 225–236 ms of
view-layer updates alone. Commit, about 90–100 ms of CPU at 2,000 cubes and
240–260 ms on the grid, is most of a small program's main-thread time, and its
own hydration still triggers one dependency-graph rebuild.

Amortized poll budget
---------------------

Every poll of a resident stage pays a fixed residency cost outside its work
budget: hydration re-runs the stage's exact validation (``validate_stage``:
Object Mode, finite geometry and transforms, and the dependency walk proving no
staged datablock aliases an original) and its material provenance snapshot;
suspension snapshots provenance again and transfers ownership. That cost grows
with the stage, not with the poll's work. In a profile of a material workload
(32 objects sharing 32 materials, 1,056 checks), 181 hydrations took 43.6 s of
61.6 s, 38.8 s of it in ``validate_stage``, while the checks themselves took
5.0 s. With a fixed 16 ms of work per poll, any slowdown of the checks (load,
profiling) also multiplies the number of polls, so staging time grows with the
square of the slowdown.

A fused poll therefore works for as long as its own hydration plus the previous
suspension when that exceeds 16 ms, at most 100 ms
(``program_jobs.fused_work_budget``, on the same budget clock as the work).
While residency costs at most 100 ms, a program's total time stays within about
twice its work plus one poll's fixed cost, and grows linearly rather than
quadratically with machine load. A program whose residency is cheap keeps the
16 ms budget. No check, validation or ownership transfer is skipped. A poll can
block the main thread for up to about 84 ms more, and only when its residency
already took longer than 16 ms; Stop still acts between polls.

Stage bundle compression
------------------------

Every suspend writes the private stage bundle with zstd compression
(``program_storage.STAGE_BUNDLE_COMPRESSION``, on by default) and every hydrate
reads it back (``agent_execution.rst``). The stage holds only the target and its
dependencies, so bundle size follows the target, not the scene.
``benchmark_blender_stage_compression.py`` runs the same one-transform,
one-check program per cohort with compression on and off, alternating the order
per round, and measures each real suspend and hydrate call; polls are split by
the instruction work clock, so both settings run the same number of cycles. Two
runs of one warmup plus five programs per setting, mean of the run medians
(compressed → uncompressed). CPU includes the writer's parallel compression
threads, so it can exceed wall time.

.. list-table:: Per call, compressed → uncompressed
   :header-rows: 1

   * - Cohort (stage target)
     - Bundle
     - Suspend CPU (ms)
     - Hydrate CPU (ms)
     - Program instructions
   * - 100 objects (16,641 vertices)
     - 365 KB → 1.22 MB
     - 6.5 → 2.4
     - 10.5 → 8.5
     - −8%
   * - 2,000 objects, shared material
     - 365 KB → 1.22 MB
     - 11.5 → 7.7
     - 18.3 → 16.7
     - −5%
   * - 511-collection tree
     - 365 KB → 1.22 MB
     - 13.9 → 10.3
     - 22.6 → 19.4
     - −5%
   * - 99,856-vertex grid
     - 2.27 MB → 6.62 MB
     - 27.9 → 6.0
     - 24.6 → 17.7
     - −15%

Uncompressed bundles would save 5–15% of a one-operation program's instructions
and 4–22 ms of CPU per suspend, at about three times the size (1.2 MB for a
16,641-vertex target, 6.6 MB at the 100,000-vertex input limit) in a private
temporary directory that holds at most three retained previews plus one writer.
Whole-program wall time differs by 1–10%, within run-to-run noise. Both settings
are ratcheted (``stage_bundle/*``). GUI fused polls and stages carrying images or
many node groups are not part of these cohorts. Fewer suspend and hydrate cycles
per program remain the larger lever.

Bundles share one private folder per process (``stage_bundle_folder``):
``private_roots.allocate`` makes it on the first suspend, with its family name,
owner token and held identity, and each suspend writes a fresh name in it; a
discard removes that bundle (and Blender's ``<file>@`` partial write) only, and
the folder goes at exit. A folder of its own per suspend cost about 1.8 million
instructions of folder creation, owner file and removal on every yield (release
gate 7ea90a2: ``stage_bundle/dense_geometry/suspend_uncompressed`` at 1.10× its
ceiling). A folder that was removed or moved is replaced on the next suspend;
the moved one stays private by its token and handle.

Request snapshots
=================

Request snapshot cost by policy
-------------------------------

``benchmark_blender_snapshot_cost.py`` calls the product's own
``ui.capture_checkpoint`` (preview-free, uncompressed
``save_as_mainfile(copy=True)`` with a window override, permissions and
metadata) on the main thread of a background factory-startup process.
Documents are synthetic: copies of one 251,001-vertex grid mesh (no UVs,
materials or images; the cheapest bytes to write), and separately 10,000
one-cube objects with their own meshes, a shared node material and a Bevel on
every tenth (per-ID work dominates). Each document is measured by its first
capture after it changed, then three repeats; every snapshot is deleted after
it is measured. Three runs; repeat medians with the range of the three run
medians.

.. list-table:: Snapshot capture, main-thread wall and CPU time
   :header-rows: 1

   * - Document
     - Saved size
     - First capture, wall (ms)
     - Repeat, wall (ms)
     - Repeat, CPU (ms)
   * - 10,000 small objects
     - 49.7 MB
     - 110–138
     - 102–120
     - 98–113
   * - 3 dense meshes
     - 48.5 MB
     - 11–21
     - 11–20
     - 10–11
   * - 12 dense meshes
     - 192.7 MB
     - 59–93
     - 29–94
     - 27–30
   * - 31 dense meshes
     - 497.2 MB
     - 67–253
     - 71–89
     - 61–66

A dense document's first capture differs from its repeats in wall time but not
in instructions, so that difference is page-cache and disk behaviour, not extra
work. The writes go to the page cache of a machine with 48 GB of memory; under
memory pressure or on a slower disk they wait for the device. Images, packed
files and linked libraries are not measured.

The two policies (``checkpoint_policy.rst``) differ per message. Every Request
captures before each message is submitted, read-only questions included, so the
UI blocks for one capture (11–253 ms for these 50–500 MB documents). Before First
Approved Edit captures nothing for a message that never edits; a message that
edits pays the same capture after approval and before its first approved edit.
Each capture is a full copy only until the background worker stores its changed
blocks (`Deduplicated snapshot storage`_), and the 2 GiB default storage budget
is enforced automatically.

Deduplicated snapshot storage
-----------------------------

Capture is the same main-thread ``save_as_mainfile`` copy; the background
snapshot worker then stores only blocks it has not stored before
(``checkpoint_policy.rst``). ``benchmark_blender_snapshot_cost.py --dedup-mb 500``
builds a 511 MB document of 34 distinct dense meshes (each its own resolution
near 250,000 vertices, with per-copy vertex noise so no two arrays match) and
200 small objects, captures it with the product ``ui.capture_checkpoint`` after
each edit below, and runs the product ``ingest_snapshot`` on a second thread.
Two runs; the second also probed the main thread every 5 ms, which slowed
ingestion. Restore is the main-thread reassembly into the working copy
(``recovery_copy``), excluding ``open_mainfile``.

.. list-table:: Per capture, 511 MB document
   :header-rows: 1

   * - Edit before capture
     - New block bytes
     - Store growth
     - Ingestion off-thread (s)
     - Restore reassembly (ms)
   * - first capture
     - 511.2 MB
     - 236.2 MB
     - 5.6 / 11.4
     - 590
   * - none
     - 0
     - 0
     - 1.17 / 1.78
     - 604
   * - move one object
     - 1,401 B
     - 234 B
     - 1.15 / 1.67
     - 560
   * - add a material slot
     - 5.97 MB
     - 2.55 MB
     - 1.25 / 1.57
     - 605
   * - add a modifier
     - 2,025 B
     - 428 B
     - 1.20 / 2.26
     - 580
   * - rotate 50 objects
     - 70 KB
     - 12 KB
     - 1.21 / 1.90
     - 561
   * - add a 4,225-vertex object
     - 9.43 MB
     - 4.47 MB
     - 1.44 / 1.83
     - 530
   * - move every vertex of one 250k mesh
     - 3.00 MB
     - 3.00 MB
     - 1.66 / 1.94
     - 587

Eight captures held 4.09 GB as full copies and 246 MB in the store. Main-thread
capture stayed 60–132 ms. A full-copy restore of the same file takes 108–132 ms,
so reassembly with whole-file verification adds about 0.4–0.5 s on 511 MB.
During ingestion a 20 µs main-thread Python task took p95 0.07–0.13 ms and at
most 2.6 ms (idle: p95 0.05 ms), so the worker's GIL use does not visibly delay
main-thread Python. Some edits store more than the edited data (material, added
object): Blender rewrites a few small blocks per save, and on the first change
after a save some mesh attribute blocks alternate between two byte states; once
both states are stored they deduplicate.

A second document, 24 identical 251,001-vertex grids and 2,000 cubes (589 MB),
saved by the native binary after each edit and ingested by the same product
code in the bundled Python 3.13 outside Blender, sets the segment policy of
``snapshot_blocks.py``. Eight snapshots used 4.71 GB as full copies and 51.6 MB
in the store (identical grids deduplicate within the first snapshot and
compress 3.7×). Store growth per edit: move one object 188 B, add a material
8.2 KB, add a modifier 389 B, rotate 50 objects 9.7 KB, add a 64-segment sphere
72.8 KB, move every vertex of a 250k mesh 610 KB; the first unchanged re-save
after the base stored 1.74 MB of alternating mesh blocks. Ingestion took
1.8–2.6 s each and restore reassembly 0.64–0.75 s. Without compression the same
store was 193 MB and restore reassembly 0.55–0.61 s; parallel decoding keeps
compressed restore close to uncompressed, so new chunks use zlib level 1 when it
saves at least an eighth.

``test_blender_snapshot_dedup.py`` restores eight real 5.4 MB snapshots exactly
(43.5 MB of full copies, 1.58 MB stored). Not measured: images or packed files
(already compressed data stores at full size), linked libraries, Windows or
Linux, and memory pressure.

Provider openings
=================

Cache-affinity keys
-------------------

The ChatGPT backend reuses a cached prompt prefix by session affinity, not by
identical bytes, and stock Codex routes every thread by its own ID, so each new
chat's first request (about 24k tokens of tools, instructions and response
format) is uncached. Angelo's agent runtime (``gpt_launch.rst``, "Agent runtime
and prompt caching") takes a stable ``cacheAffinityKey`` per account and static
prefix instead. The key leaves out every path and identity
(``cache_affinity``): a key that digested the thread's working directory would
differ in every chat and read nothing cached. Measured on the ChatGPT backend:
once a key had been used once, keyed fresh threads had 24,320 of their first
request's 24,438 input tokens cached (99.5%), also from a new app-server
process; keyless fresh threads had none.

It replaces the warm bases below on that runtime: no warm-up request (about
25k tokens per variant, connection, profile and Codex build, remembered across
launches), no ephemeral prediction forks (which a restart could not resume),
and no race with a warm-up still in flight on a cold start, where the bases
could not help. Those mechanisms remain for the person's own Codex. What still
precedes the per-chat content and is not shared across launches is only
Codex's environment context (about 170 tokens: the working directory and the
date), which follows the cached prefix.

With ``gpt-6.1-sol`` at low effort on the default task set (five keyed trials
and five on stock Codex, interleaved, each a fresh Angelo process, read from
each trial's performance receipts), every keyed first request after the key's
first use had 23,424 of about 25,000 input tokens cached (93.5–94.1%; the
environment context and the chat's first message follow the prefix). A keyed
trial left about 4.2k input tokens uncached; a trial on stock Codex left about
28.6k in most trials, its warm-up included.

One route per account. Every chat of one account, model and prefix sends one
key, and OpenAI routes by it: its prompt-caching guide documents that a prefix
and key beyond about 15 requests a minute overflow to further machines, which
do not have the prefix cached. One person's chats stay far below that; many
parallel chats on one account can approach it, and their first requests then
read the opening less often. The key is the same from every machine of that
user only with ``ANGELO_CACHE_KEY_SECRET`` (``gpt_launch.rst``); otherwise each
machine's secret gives it its own route.

Shared Codex app-server
-----------------------

ChatGPT subscription chats share one app-server per connection and, on the
person's own Codex, fork a warm base thread for each new chat
(``agent_integration.rst``, "Shared Codex app-server and forked openings"; on
the agent runtime, `Cache-affinity keys`_ replace the bases). A connect then skips the app-server start and
``initialize``; the account, configuration and model checks still run.

``codex_runtime_check.py`` drives new chats through ``Controller.send``, one
controller per chat as the add-on makes them, against the installed codex-cli
and a local fake endpoint (no model is called). After a first chat whose
completed turn warms the base, two arms alternate B A A B B A A B B A, five
chats each: A leases the shared app-server and forks the base; B turns sharing
off, so each chat starts its own app-server and thread. Medians of five new
chats per arm, three runs, milliseconds (A / B):

.. list-table:: New chats, shared (A) against separate (B) app-servers
   :header-rows: 1

   * - Run
     - Connect (``runtime_start_ms``)
     - First text
     - Request total
   * - 1
     - 472 / 899
     - 783 / 2,112
     - 793 / 2,160
   * - 2
     - 686 / 844
     - 732 / 1,443
     - 736 / 1,540
   * - 3
     - 436 / 978
     - 537 / 1,967
     - 543 / 2,261

A cold app-server start takes 1.2–4.6 s. In one warm process a fork costs what a
``thread/start`` does (``codex_fork_probe.py --timing 8``: medians 318 and
401 ms), while resuming a base in a new process and forking it took 1,032 ms
against 294 ms for a start, which is why chats share the process instead. Five
separate app-servers held 498–511 MB resident each, the shared one 114–121 MB.

In every run all five A chats were forks whose requests sent the base's thread
ID as ``prompt_cache_key`` and ``session-id`` and their own as ``thread-id``;
each receipt counted only its own request, not the base's; a host tool call made
inside a fork reached its chat and completed; a chat answered 3 s late did not
delay another chat's turn (19–35 ms); and after a restart the forked chat's next
message went to a new thread whose first request carried its earlier message,
while the remembered base was resumed with no model request and forked by the
next new chat.

Against the live service (low effort), a fork's first request had 24,064 of its
24,426 input tokens cached (98.5%), while a chat on a fresh thread had none of
24,388 cached; connect took a median 209 against 1,114 ms and request total
6.88 against 8.33 s, the model's own time dominating. A fork's first request is
38 tokens longer (the warm-up exchange). Each base costs one warm-up request
per variant (model, instructions, tools, response format; never effort) per
connection, profile and Codex build, remembered across launches, and each
warm-up leaves its own receipt (`Eager openings`_).

Fixed opening of a new chat
---------------------------

Later requests of a chat reuse the whole previous prompt from the provider's
cache, so most of a session's uncached input is the first request of each new
chat. These probes measure that opening without a model:

* ``codex_prompt_probe.py OUT --variant plan --task edit --model MODEL``
  (codex-cli against a local fake endpoint) records the first request of a
  typed-plan thread. In prompt order: the code-mode tool item (Codex's ``exec``
  preamble, about 5.2 KB; Angelo's tool declarations, about 21 KB; Codex's own
  nested, collaboration, ``wait`` and ``request_user_input`` tools, about
  12.9 KB), Angelo's instructions (about 22 KB), Codex's multi-agent role and
  mode (about 3 KB), the environment context (805 B: working directory, shell,
  date, timezone), the user message, and the strict response format (about
  35 KB), about 102 KB in all. The request's ``prompt_cache_key`` is the thread
  ID; stock Codex's app-server protocol (``codex app-server
  generate-json-schema``) has no parameter and the binary no configuration key
  that sets it, and the retention options are ignored (no retention field is
  sent). Angelo's agent runtime adds the parameter, ``cacheAffinityKey``
  (`Cache-affinity keys`_).
* ``codex_prompt_probe.py … --fork plain|before|prediction`` makes the chat with
  ``thread/fork`` from a base thread that ran one warm-up turn. A ``plain`` or
  ``before`` fork sends its own thread ID as the key. A fork with
  ``experimentalPredictionMode`` (which requires ``ephemeral`` and
  ``excludeTurns``) sends the base thread's ID on every request, and its first
  request repeats the base's warm-up request item for item before the new
  message. Such a fork is not persisted (after an app-server restart
  ``thread/resume`` answers "no rollout found"), and a base that never ran a
  turn cannot be forked this way. A prediction fork sends its base's thread ID
  as ``session-id`` and its own as ``thread-id``; every other chat sends its own
  ID in both.
* ``codex_cache_fork_check.py`` runs the same comparison against the live
  service: after one warm-up turn on a base thread, each of five
  prediction-mode forks had 23,680 of its first request's 23,906 input tokens
  cached, and each of five fresh threads with the same opening and first
  message had none of 23,870 cached. Cache reuse follows the session key, not
  identical bytes. A cached opening saved about 0.37 s to completion; time to
  the first streamed event showed no reliable difference.
* ``claude_prompt_probe.py OUT`` (an API-key launch with a dummy key against a
  local fake endpoint; Claude Code 2.1.293) records about 82 KB. Tools come
  first: WebFetch and WebSearch (1.9 KB) and the StructuredOutput tool carrying
  the plan schema (about 55 KB). Then the system blocks: a billing header,
  Claude Code's identity line (cache breakpoint), and one block with Claude
  Code's default prompt (3.3 KB) followed by Angelo's instructions (about
  12 KB; cache breakpoint). That cache matches by prefix with no per-session key, so a new
  chat reuses another chat's opening within the cache TTL. Every Claude
  connection launches alike, so a subscription's opening has the same parts.
  ``--route`` (default ``claude_api``) builds the launch environment of that
  route as Angelo does, its prompt-cache TTL included (``model_routing.rst``,
  "Prompt-cache TTL per session"); ``--route claude_subscription`` keeps the
  dummy key (a subscription's sign-in is never read) and shows only that
  route's TTL.
* No Claude route runs ``--bare``. Bare mode would drop the web tools, Claude
  Code's default prompt and the first message's environment section. The cost
  of keeping them was measured in unbilled captures of the team launch with
  and without ``--bare``: the cached prefix grows by 5,336 B, the first message
  by about 1.1 KB, and the first ``initialize`` answer takes 34 ms longer
  (median of seven alternating local launches each, 0.243 s vs 0.209 s). These
  bytes are what subscription chats always sent, and they are the same for
  every member of a team.
* ``prefix_composition.py`` splits Angelo's parts by section, tool and action
  kind (marginal bytes, shared definitions included).

Two rules keep the opening small and stable. A typed-plan Codex thread's
program reference names the action kinds and checks that its response format
already states exactly, instead of stating them twice (``agent_integration.rst``,
"Codex typed plans"); this saves 3,643 B of instructions (25,584 → 21,941 B;
team threads 25,737 → 22,094 B). Claude sessions start with
``--exclude-dynamic-system-prompt-sections``, so the block holding Angelo's
instructions carries no working directory or date and is the same bytes on
every day and install, which keeps it cacheable across chats.

That flag moves Claude Code's per-session context into the first user message,
which each new chat writes to the cache once, at the session's TTL rate (2x
input at 1h), for its later requests to read. Angelo starts every Claude
session without two of its sections (``runtime_environment.SESSION_CONTEXT``:
``CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`` and
``CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS=1``; an inherited copy is dropped): the
auto-memory instructions (about 2.4 KB), which tell the model to keep memory
files with a Write tool an Angelo session does not have, and the git status
snapshot of the repository holding the chat's workspace (up to about 2.7 KB of
unrelated file names). In a recorded run with both sections present, each new
chat's first request read 33,381 cached tokens (Claude Code's prompt, the plan
schema and Angelo's instructions) and wrote 4,006–4,012 at the 1-hour rate.
That message was 9,779 characters: Angelo's message 3,043 (request,
observation, workspace line, task memory, collaborators), the git snapshot
2,669 (the workspace sat inside a repository), memory 2,355, environment 829,
account e-mail 264, and the model, token-budget and date lines 311. By
``token_accounting.py``'s byte-class fit to the same chats' 193 later cache
writes, scaled to the recorded first write, the two removed sections were
about 2.1k of the 4.0k tokens (snapshot about 1.3k, memory about 0.8k; an
estimate, not a token count), so about 0.8k where the workspace is not in a
repository. Angelo's first message carries object revisions, so no two chats
share a first message and each writes its own.

Angelo's own blocks of that message are smaller too: an empty task memory is
one line (``task_memory.rst``) and the collaborator list is compact JSON with
models as ``{id: name}`` (``agent_integration.rst``, "Per-message context
deltas"): 1,431 characters become 752, about 0.3k fewer written tokens per new
chat by the same fit. Both sit after the cached prefix, which is unchanged.

What remains in the opening, and why:

* Checks and core action-kind parameters stay in the Codex response format
  (about 5 KB and 12 KB) so that every common edit keeps constrained decoding;
  as JSON text in the reference they would cost escaped output tokens instead.
* Codex's ``request_user_input`` tool has no supported switch:
  ``tools.experimental_request_user_input`` stops the app-server. Its own
  multi-agent role, six collaboration tools and mode note are off with
  ``agents.enabled=false`` (8,330 B of the first request, ``gpt_launch.rst``);
  ``features.multi_agent`` and ``features.multi_agent_v2`` alone left them on,
  since the model catalog forces ``multi_agent_version: v2``.
* Codex's environment context (805 B) carries the date and timezone the model
  sees.
* Claude's plan schema carries numeric bounds and defaults (about 5 KB); its
  opening is already reused across chats within the cache TTL.

Eager openings
--------------

On the person's own Codex (the agent runtime has no bases and only starts and
checks the shared app-server here, its keyed threads reading the opening
instead: `Cache-affinity keys`_), a chat's forked opening is readied before
its first message
(``agent_integration.rst``, "Shared Codex app-server and forked openings"):

* When the chat's connection is ready (its model catalog loaded at launch,
  after a sign-in or a refresh: ``codex_opening.prepare``), the shared
  app-server starts off the chat's path, runs its checks, and resumes the
  remembered base (no model request) or warms one. The first chat of a launch
  forks it.
* A chat that arrives while its base is still being readied waits at most
  ``codex_bases.PREFILL_SAVING_SECONDS`` (0.4 s), and only when the base is
  expected within that time (``codex_bases.wait_bound``); otherwise it starts
  its own thread at once.
* Every warm-up turn leaves a receipt (``performance.json``, ``kind``
  ``warm_up``) with its usage, and benchmarks add it to the task during which
  it was recorded, so a cost comparison omits no request.

The bound: a chat should wait only when the base's expected remaining time is
smaller than what a cached opening saves the model. Measured savings were
0.37 s (completion of a fork against a fresh thread, ``codex_cache_fork_check.py``)
and 0.45 s (first text after connect, ``codex_runtime_check.py``); the smaller,
rounded to 0.4 s, is both the threshold and the longest wait. A remembered
base's ``thread/resume`` in a new app-server takes about 0.56 s
(``codex_fork_probe.py``; ``codex_bases.RESUME_SECONDS``); in six fresh
processes a resume and fork took a median 1,043 ms against 541 ms for a
``thread/start``, with a slow tail (271 ms to 4.3 s). A resume expected within
0.4 s is waited for; an earlier or overdue one is not. A warm-up is a model
request (several seconds) with no such estimate, so a chat waits for it only
once its reply or usage has arrived; a chat whose message comes while the model
is still answering the warm-up starts its own thread and pays its opening
uncached, and the base serves the next chat. No keep-alive request keeps a
cache warm: a base older than the provider's cache TTL writes it again on its
next fork.

Guards (``test_native_codex_opening_guards.py``): a warm-up that made no base is
sent at most once per launch, and not again for a day after a verdict (an
unclean reply, a tool call) or for 15 minutes after a failed turn without one (a
stream error, a 5xx); a second instance on the same profile warms nothing at
connect and keeps the first one's base; the opening waits for a team sign-in
being restored; and the scope ignores the launching terminal and the macOS
version, so neither creates a new scope and a new warm-up.

``codex_opening_check.py`` measures the path without a model: one launch per
trial (a new pool, the installed codex-cli against a local fake endpoint), the
chat made at launch and one message sent ``--delay`` seconds later through
``Controller.send``. ``before`` prepares nothing until the message; ``after``
calls ``codex_opening.prepare`` at launch as a loaded catalog does. ``stored``
has a base an earlier launch remembered in the state folder, ``none`` a new
state folder. Medians of three launches per arm, alternating, milliseconds:

.. list-table:: First chat of a launch, message 10 s after launch
   :header-rows: 1

   * - Case
     - Connect (``runtime_start_ms``)
     - First text
     - Request total
     - Forked
     - Model requests per launch
   * - Remembered base, not prepared
     - 1,305
     - 2,336
     - 2,630
     - 0 of 3
     - 1 (the chat's, uncached)
   * - Remembered base, prepared
     - 603
     - 782
     - 815
     - 3 of 3
     - 1 (the chat's, with the base's key)
   * - Nothing remembered, not prepared
     - 901
     - 1,456
     - 1,654
     - 0 of 3
     - 2 (the chat's, then the warm-up)
   * - Nothing remembered, prepared
     - 337
     - 393
     - 401
     - 3 of 3
     - 2 (the warm-up, then the chat's fork)

The fake endpoint answers at once, so these times measure the path, not the
model: most of the saving is the app-server start, ``initialize`` and the checks
leaving the chat's critical path, which the connection pays at launch instead.
The cached prefill saves model time on top (about 0.4 s). A message 3 s after
launch races the opening: under heavy load some chats find the base still
being readied and start their own threads, as without preparation. With the
fake answering a warm-up 20 s late and the message 12 s after launch, no
prepared chat waited for the warm-up or forked it, and none was slower than
without preparation; each launch then still sends two uncached model requests,
the chat's opening and the warm-up.

Token diet
==========

Every Claude typed-plan call carries Claude Code's own system prompt,
Angelo's appended instructions and the plan's JSON Schema
(``initialize.jsonSchema``). The schema is the largest of the three, so
``plan_schema.py`` keeps exact schemas only for the tools, program action kinds
and check kinds most turns use, and names the rest (``agent_integration.rst``,
"Deferred plan schemas"). Deferral moves only where the model reads a schema:
``tool_specs.validate_call`` still validates every operation exactly before
anything runs.

Method: eight single Claude Code calls with Angelo's exact launch flags and
``initialize`` payload (``claude-opus-5-5``, ``--effort low``, a fresh empty
working directory, the prompt "Reply OK" unless stated). Input is Claude Code's
reported ``input_tokens`` plus cache writes and cache reads. A call with the
full schema, one with an empty-operations schema (227 bytes) and one without
appended instructions separate the parts.

.. csv-table:: First-call composition, tokens
   :header: "Part", "Before the diet", "After the diet"

   "Claude Code system prompt, WebSearch/WebFetch and the structured-output tool", "4,325", "4,325 (same runtime and flags)"
   "Appended instructions", "4,628 (13,921 bytes; 3.0 bytes per token)", "about 2,900 (8,753 bytes)"
   "Plan schema", "51,805 (99,770 bytes; 1.93 bytes per token)", "about 20,360 (39,392 bytes)"
   "Total (""Reply OK"")", "60,758", "27,587 (−55%)"

JSON Schema is dense in tokens: at 1.93 bytes per token the schema was 85% of
the first call, and ``angelo_scene_program`` alone was 66 KB of it. The
after-diet split applies the before-diet bytes-per-token ratios; instructions
and schema measured 23,262 tokens together. Later calls read 22,278 tokens of
their prefix from the prompt cache that an earlier call wrote, so a change to
the instructions alone does not invalidate the cached schema.

The levers; each schema figure is what removing that lever alone from the
diet's schema would add back:

* Deferred families: the sculpt and Media Studio tools,
  ``angelo_memory_update`` and ``angelo_expand_recipe`` are named, not
  specified (15.1 KB).
* A smaller ``angelo_scene_program``: the common action kinds and their checks
  stay exact, the others are named (31.6 KB). Repeated fragments of 100 bytes
  or more are shared, where the default is 200 (4.3 KB). Dropping
  validation-neutral redundancy saves the rest.
* Instructions: the sculpt and Media Studio guidance (6.0 KB) arrives with
  those families' schemas; the plan instructions explain the lookup (0.7 KB).
* ``angelo_agent_capabilities`` results do not repeat the action catalog:
  ``SUMMARY`` is about 5.7 KB (formerly about 65 KB), and a named lookup such
  as ``light_create`` returns 1.8 KB.

A deferred family costs one lookup round, about 2–5 s, the first time a
session uses it, which is why small, frequently used kinds are exact
(`Everyday kinds`_). A plan the host rejects comes back with the exact schemas
of the deferred items and action parameters it submitted incorrectly.

Current budgets (``tests/python/angelo_agent/plan_budgets.py``, enforced by
``test_native_plan_schema.py``; compact JSON):

* Claude plan schema: 55,170 bytes.
* Claude instructions sent with the plan: 11,980 bytes.
* Every tool declared in full, as voice and external Codex threads declare
  them: 102,000 bytes.

A budget moves only with a toolset entry in ``agent_integration.rst`` that
states the measured size. ``coverage_prefix.py`` prints the always-sent schema
sizes, with token estimates at 1.93 bytes per schema token; ``--fit RUN_DIR``
adds tokens from ``token_accounting.py``'s fitted approximation (`ID diet`_).

Limits: single calls measure tokens, not latency or task success over a
session.

Cache diet
==========

A resumed native session already holds the context blocks of its earlier
messages. Each message is written to the provider's prompt cache once and read
again by every later call, so whatever a message repeats is paid again on
every later call of the session. A Claude session is therefore sent only what
it does not already hold (``agent_integration.rst``, "Per-message context
deltas"), and the host derives the checks that a program's operations already
state (``local_verification_and_recipes.rst``):

* Handoff: a host-certified edit whose completion the host composed is handed
  off as a compact receipt (post-commit handles and revisions, preview and
  view IDs, changed names) followed by the host's summary row. Failures,
  partial or uncertain outcomes and anything the host did not summarize keep
  their full results.
* Task memory: a delta against what the native context received; receipts of
  tool results it already holds are counted, not repeated.
* Collaborator list and file-workspace line: only when they change.
* Observation: one line when identical; otherwise only the changed object
  entries, with the constant turn-context notes named, not repeated.
* Plans: ``expected_checks`` restate only results the operations do not state.

Method: ``replay_turn_context.py`` rebuilds every message of a recorded run
directory through the current composition, as a resumed Claude session
receives it. Through each toolset's own code it reproduces the recorded
messages byte for byte, which checks the replay. Cache-write tokens are
estimated at 1.97 bytes per cache-write token, fitted to recorded turns, and
include the previous plan, which also enters the history.

What a message adds with deltas (five recorded five-task trials, rebuilt):

* after a host-certified edit, for a question or an edit: about 1.2–1.3k
  tokens (the receipt, the host summary, a one-line memory update and the
  observation), where repeating complete blocks wrote 4.3–6.2k;
* after a question: about 0.3k, plus the collaborator list the first time;
* after a failed or partial edit: about 3k, because the full result (at most
  5,000 characters) stays in the handoff for the model to reason about;
* after a preview the person accepted in the UI: the accept's receipt and a
  host-composed summary instead of its full result.

`ID diet`_ and `Turn diet`_ reduce these further; the latter has the current
per-task figures. Checks the host derives remove an estimated 330–430 bytes of
restated checks per five-task trial from the plans (about 170–220 output
tokens); a model given the guidance may write different plans.

**The prompt prefix.** The appended instructions and the plan schema are
byte-identical across chats, workspaces and sessions
(``test_native_cache_diet.py``). Claude Code renders its environment (working
directory, platform, date) as an attachment of the first user message, yet
about 5k tokens of its own prompt depend on the working directory: the first
call in a new working directory wrote about 5.9k tokens, of which about 0.9k
was the first message, while Claude Code runs that reused one working
directory wrote none of it after their second trial.
Angelo therefore launches Claude Code in the state folder's ``workspace``, one
path for every chat of a profile. ``eval_live_agent.py --stable-state-dir``
(``bench_mcp_vs_angelo.py --angelo-stable-workspace``) runs every trial's fresh
state at one path, so trials measure what ordinary use pays.

**Long sessions.** Before deltas, every message also repeated task-memory
receipts that grew about 430 tokens per earlier edit, so a session passed 160k
tokens of context after about 25 tasks and the runtime compacted it
repeatedly. With deltas a session grows only by what each task adds.

ID diet
=======

In recorded sessions before short identifiers, every turn wrote 1.5–1.85k
tokens to the prompt cache and every edit output 336–583 tokens. What filled
them:

* identifiers: every handle carried the 32-hex document epoch, every revision
  was 64 hex digits, a transform program emitted its handle four times and its
  revision twice, and each turn's observation and handoff repeated them;
* ``read_set`` repeating the ``write_set`` entry, and ``"verify": false``;
* constant prose repeated in every delta: ``objects_scope``,
  ``static_notes``, the task-memory header and receipt counts, the
  untrusted-data preambles;
* a handoff stating each certified result twice, as a receipt and as the
  host's multi-line summary;
* 30–40-token plan replies.

Toolset 19 changes the contract (``agent_integration.rst``, "Short
identifiers" and "Per-message context deltas"): short ``o<n>`` handles and
eight-digit revision prefixes, mapped back to the unchanged long forms by one
resolver (``identifiers.expand_call``); optional ``read_set``/``write_set``/
``verify``; observations without constant notes, false flags or no-omission
reports, and deltas of changed state fields only; one handoff entry per
certified result; a one-line task-memory delta; short delta labels; and edit
replies of about 15 words. The appended instructions grew by about 1,000 bytes
(the turn-context notes and the conventions) while the plan schema shrank by
about 490 bytes, so every call re-reads about 238 more prefix tokens.

Effect, estimated by token accounting over recorded five-task trials rebuilt
through the toolset-19 composition, with each recorded plan rewritten
deterministically to the new contract (replies and other model choices as
recorded): identifiers in a turn's cache write fell from 159–335 tokens to
15–24, identifiers in an edit's output from 57–167 to 4–6, and an edit turn's
whole cache write from about 1.5–1.86k to 0.67–1.0k tokens. Structure fell by
about 300–400 tokens per write, because unchanged fields are not resent and the
handoff states each result once.

**Token accounting.** ``token_accounting.py`` reads a run directory and
attributes each call's output and cache write to identifiers (handles,
revisions and other digests, UUIDs, tokens), prose (non-JSON lines and JSON
strings of four or more words) and structure (everything else). Its tokenizer
approximation counts three byte classes (hex runs, ASCII letters, other bytes)
at rates fitted by least squares to the run's own recorded output and
cache-write counts, with one constant for the tool-call framing and one for the
per-turn wrapper. On a five-task recording the fit was 0.714 tokens per hex
character, 0.464 per letter and 0.482 per other byte, with residuals of rms
14.8 tokens on a mean of 376 output tokens and rms 23.5 on a mean of 1,722
written tokens. It approximates Claude's tokenizer; it is not the tokenizer.
The "after" side rebuilds each message with ``replay_turn_context.py`` and
rewrites each recorded plan to the current contract with the replayed
context's own identifier table. Scenario flags such as ``--reply-words 15``
are assumptions about the model and are printed with the result.

**Safety.** Short identifiers are a transport detail: the resolver expands them
before validation, and every owner checks the long forms exactly, so stale
detection, revision checks, host verification, staging, preview/accept and the
one Undo step are unchanged (``test_native_id_diet.py`` runs the real
staleness rules over expanded short forms).

* Epoch change: a number denotes one long handle, epoch included, for the
  lifetime of its native context's table, and is never reused. After Undo or a
  reload the same object is shown under a new number; an old number expands to
  its old long handle and ``resolve_object`` rejects it as expired. It is never
  re-bound to the new epoch or another object, whether or not the model saw the
  new observation.
* Revision collisions: a prefix expands only to a revision this context was
  shown beside that handle (at most the last 8); a prefix shared by two of them
  is rejected as ambiguous, and the host shows such a revision whole. The
  expanded revision is then compared exactly, so a stale prefix expands to its
  own stale revision and is rejected. The one residual case: a revision evicted
  after eight newer ones of the same object were shown, reused by the model,
  whose eight-digit prefix equals a later shown revision's (probability
  2\ :sup:`-32`, about 2.3 × 10\ :sup:`-10`, per such reuse); it would bind
  the later revision, which the host showed this context, not an unseen state.
  Handle numbers have no collisions.
* Lost or unavailable tables: a resumed context without its table sees and
  accepts long forms only (its history may hold numbers whose meaning was
  lost), and any short form is rejected before validation. Voice threads,
  collaborators and the external CLI keep long forms; Codex chat threads have
  tables from toolset 27.
* Long forms stay valid everywhere. A Claude session recorded before toolset
  19 resumes with a fresh table and one complete message.

Rerunning the accounting (offline, about a second)::

    python3 tests/python/angelo_agent/token_accounting.py RUN_DIR [--toolset N] \
        [--fit-also OTHER_RUN ...] [--reply-words 15] [--omit-edit-replies] \
        [--answer-from-observation] [--tasks 5] [--json OUT]
    python3 tests/python/angelo_agent/replay_turn_context.py RUN_DIR
    python3 tests/python/angelo_agent/long_session_cost_model.py [--tasks 40 100] [--rebase 40000]

``RUN_DIR`` is an ``eval_live_agent.py`` or ``bench_mcp_vs_angelo.py`` output
folder (``angelo-trial-NN-transcript.jsonl`` plus ``angelo-trial-NN/``).
``--answer-from-observation`` collapses a recorded lookup-then-answer question
into one call; ``replay_turn_context.replay_trial(..., rebase_at=(N,))``
rebuilds turn N as a rebased first message.

**Adding to the contract.** The identifier contract lives in
``identifiers.py`` and ``agent_integration.rst`` ("Short identifiers"); message
composition in ``context_ledger.py``, ``routing.handoff`` and
``claude_outcomes.terse_receipt``; their tests in ``test_native_id_diet.py``,
``test_native_cache_diet.py`` and ``test_native_round_savings.py``. A new action
parameter that names an object must use ``action_schema_types.HANDLE`` (a new
check field, ``validation_spec.TARGET``) and be listed by
``affected_references`` (or ``check_references``); the resolver then covers it,
and ``test_native_id_diet.py`` shows how to pin it. Measure a composition
change with ``token_accounting.py`` first; a change to the replay itself must
still reproduce the recorded messages through the old code path.

Turn diet
=========

Long sessions have shapes that five-task trials miss. Recorded 40-task chats
(``eval_live_tasks.tasks_for("long")``: a question, a cube, then cycles of
move, resize, bevel, material and a size question; ``claude-opus-5-5``, effort
low, stable working directories) showed:

* before toolset 19, every size question took two calls (a lookup, then a
  continuation that re-read the whole context), writing about 4.1k tokens;
* at toolset 19 a question took one call (the model computed dimensions from
  its history), and a session grew about 0.8k tokens per task: per task, 519
  (move), 751 (resize), 847 (bevel), 940 (material) and 1,038 (question)
  tokens written and 149–382 output.

Toolset 20 (``agent_integration.rst``, "Inferred bindings"): no
``read_set``/``write_set``, recipe revision or verification read set for
objects the latest observation shows; dimensions, scale and rotation on
observed entries, so size questions take one call without arithmetic over the
history; an optional edit reply; and a smaller suffix: own-commit change hints
left out, changed entries as their changed fields, continuation observations as
deltas, certified results without identities the observation already shows, no
bookkeeping-only memory lines, grouped datablocks and a shorter handoff label.
The instructions grew about 0.5 KB (+143 prefix tokens per call).

Method: ``token_accounting.py --toolset 20`` rebuilds each recorded toolset-19
message through the toolset-20 composition and rewrites each recorded plan to
the toolset-20 contract; the replayed context's own identifier table decides
which bindings the host would infer. ``replay_turn_context.py`` restores the
recording's short forms from the chat's identifier table and reproduces every
recorded toolset-18 and toolset-19 message byte for byte with that toolset's
own code (278 messages over four recorded runs). Tokens use the three-class
approximation fitted to those four runs (0.639 tokens per hex character, 0.295
per letter, 0.736 per other byte; residuals rms 15 output and 34 cache-write
tokens). The prefix growth is included.

.. csv-table:: Per task at toolset 20, estimated (tokens; means of complete 40-task recordings)
   :header: "Task", "Cache write", "Output", "Output, reply omitted"

   "Move", "462", "203", "179"
   "Resize", "460", "266", "239"
   "Bevel", "538", "355", "269"
   "Material", "638", "288", "250"
   "Question", "598", "149", "149"

A session now grows about 0.55k tokens per task. These are estimates: a model
given the new instructions may write different plans, replies or lookups.

Context rebase measurements
---------------------------

A context rebase (``agent_integration.rst``, "Context rebase"; off by default)
continues a long Claude chat in a fresh native session seeded from host state.
In a recorded 40-task session with a rebase at 50k tokens:

* The cached prompt prefix stayed a cache hit: the first call after each
  rebase read exactly the 27.0k-token prefix (one working directory).
* With the earlier limits each rebase wrote about 12.6k tokens: the seeded
  first message was 26.2 KB, of which the handoff was 16.0 KB (16,000
  characters from the start of the chat), task memory 4.9 KB (all 24
  receipts), the request list 3.2 KB, the observation 1.0 KB and the
  collaborator list 0.9 KB.
* The session finished in 131.9 s against 180.6 s without a rebase, because
  every later call re-read a smaller context.

The current rebase message, rebuilt for the same turn, is 4.8 KB, about 2.1k
tokens (1.9k more than that turn's ordinary message): the handoff covers the
last turn only (``context_rebase.HANDOFF_LIMIT``, 4,000 characters), historical
receipts are counted, and the request list keeps 1,500 characters. With that
message the long-session model below puts the cheapest threshold at about 40k
tokens; it changes little at 40 tasks and reduces modelled cost by about 17% at
100. The new context keeps only the last 1,500 characters of requests, the last
reply, open previews, the brief and the scene, so older chat nuance is gone
(task-memory receipts stay readable through ``angelo_memory_read``). Short
handles and inferred bindings never carry over: the new context gets a fresh
identifier table (``test_native_turn_diet.py``).

Long-session model
------------------

``long_session_cost_model.py`` is deterministic and makes no provider call. It
plays the long benchmark's task sequence in one native context with per-kind
shapes (model calls, tokens written to the prompt cache, output tokens) taken
from recorded long sessions, and accounts every call as the prompt cache does:
a call reads the prefix plus every earlier write in the context. It also
models auto-compaction and the context rebase. ``validate`` checks the model
against the recorded 40-task runs: within 3% without a rebase and within 7% for
the one run with a rebase (``test_native_long_session.py``). The shapes must
come from long sessions: five-task shapes underestimated a question's calls
and writes, an edit's writes and output, and the prefix.

Where to look when a measured turn is larger
--------------------------------------------

* Output per edit above about 300 tokens: read the plans. If the model still
  writes bindings, long forms, a repeated ``read_set`` or long replies, check
  that its first message showed the current instructions and short forms;
  replies are governed by ``CLAUDE_PLAN_INSTRUCTIONS`` (``providers.py``).
* Cache write per task above about 0.6k: rebuild the run with
  ``replay_turn_context.py`` and look at the blocks. The remaining large items
  are the plan JSON in the history (about 200–350 tokens per edit), the
  verified line (30–60) and changed observation entries.
* A size question taking more than one call: check that the observation lists
  ``dimensions`` for the object (it must get a revision: active, mentioned,
  selected or recently changed).
* Rejected plans mentioning "Unknown object handle" or "not shown": the model
  is guessing or shortening identifiers itself; check the table's persistence
  (``runtime_sessions[route]["identifiers"]``) and the instruction wording.

Inference lives in ``identifiers.py`` (``_infer*``, ``observe``,
``INFERRED_FIELDS``), the suffix in ``context_ledger.py``
(``compact_observation``, ``_explained``, ``_entry_delta``,
``observation_value``), ``claude_outcomes.terse_receipt``,
``memory_context.render_delta`` and ``context_rebase.py``; their tests are
``test_native_turn_diet.py``, ``test_native_id_diet.py`` and
``test_native_cache_diet.py``. Update the model's ``SIDES`` from each new
recorded long run so ``validate`` keeps it honest.

Everyday kinds
==============

A 64-request chat of ordinary requests (``eval_live_tasks.tasks_for("survey")``,
``eval_survey_tasks.py``; Opus 5.5, effort low) paid 24 deferred-schema lookup
calls. Fourteen of them named only small, everyday action kinds, and each cost
one model round (2.4–4.3 s, about 3.4 s each). A session pays such a round the
first time it uses a deferred kind, so short sessions pay it on a larger share
of requests than one long chat.

Claude's plan therefore states these kinds exactly
(``plan_kinds.CLAUDE_EVERYDAY_ACTIONS``): ``object_delete``,
``object_rename``, ``modifier_apply``, ``collection_link``, ``object_join``,
``world_configure``, ``object_duplicate``, ``marker_edit``,
``camera_configure``, ``property_set``, ``light_configure``,
``armature_bind``, ``mesh_extrude``, ``camera_create``, ``light_create``,
``uv_smart_project`` and, from toolset 36, ``material_assign`` (676 bytes).
``mesh_bend`` is named only (709 bytes stated; `Claude's plan states
mesh_bend`_, `Concepts, not scenes`_). ``batch``
is exact too, with its placement note (`Plan output tokens`_). The large kinds
(``shader_nodes``, ``geometry_nodes``, ``create_object``, ``node_edit``,
``operator_run``) stay deferred.

* Cost: the first sixteen kinds add 9,734 bytes to the plan schema (about
  5,040 tokens at 1.93 bytes per token); with the typed torus and a check
  description added at the same time the schema grew from 40,463 to 50,918
  bytes. The plan is part of the cached prefix, so later calls read these
  bytes from the cache; a session's first call writes them once.
* Return: one model round of about 3.5 s on each request whose first use of one
  of these kinds would otherwise look it up; 14 of 64 requests in the survey.
* Codex: the strict plan response format keeps the short core
  (``PLAN_CORE_ACTIONS``); a Codex chat thread's program reference states the
  everyday kinds (``mesh_bend`` among them: `One-shot rounds`_),
  ``create_object``, ``batch`` and ``transform_apply`` as TypeScript lines instead
  (``codex_reference``; `Fixed opening of a new chat`_). ``transform_apply``
  is there because GPT-6.1-Sol applies a scaled
  object's scale before beveling it: in recorded gate runs it looked the kind
  up in 7 of 10 ``bevel_and_shade`` turns and GPT-6 Astra in 3 of 3 long-set
  ``bevel_03`` turns (8-13 s against about 5 s), one model round each, for
  88 B of reference (Claude never used it, so its plan keeps it
  deferred). Rejection prompts use the list of the runtime that planned
  (``rejection_prompt(..., core)``).

Plan output tokens
==================

On a multi-object build a one-round plan's wall time waits on what the model
writes. On the one-shot "slightly arched stone footbridge" (Opus 5.5, effort
low, five trials) Angelo wrote one plan of 1,723-2,013 output tokens (median
1,853, of which 453-752 thinking) in 17.9-23.9 s (median 19.3 s): the rounds a
single plan saves are spent on its output.

**Method.** ``plan_output_accounting.py`` (offline, about a second) reads a
run directory's recorded calls: Claude transcripts, and for Codex the rollouts
of the run's threads in the connection's Codex home. It splits each plan's
compact JSON into parts and counts each part three ways: a per-class fit
(digits, letters, whitespace, other bytes and a per-call constant, fitted by
least squares to the recorded visible output), tiktoken's ``o200k_base`` and
bytes/4. Claude's tokenizer spends about one token per digit: on 586 recorded
Opus calls the fit was 1.02 tokens per digit, 0.34 per letter, 0.72 per other
byte and 50 per call (rms 8.3 on a mean of 213), so numbers cost about twice
their o200k count. For GPT-6.x, o200k is exact: every recorded final message
counted o200k + 10. Per-part counts tokenize parts separately and sum to about
the whole.

.. csv-table:: Recorded Opus plan output per call by part (fit tokens, means)
   :header: "Part", "Arched footbridge", "Footbridge", "Lighting", "Long session (200 calls)"

   "Thinking (recorded)", "557", "244", "198", "5"
   "Python script and its description", "390", "0", "0", "0"
   "Numbers", "195", "188", "71", "7"
   "Argument names", "188", "196", "194", "9"
   "Braces, plan and tool wrapper, mode", "187", "165", "129", "51"
   "Operation kinds", "83", "87", "48", "8"
   "Reply", "73", "70", "0", "24"
   "Names and aliases", "87", "97", "17", "2"
   "Upper-case constants", "18", "20", "26", "1"
   "Values equal to their default", "5", "6", "9", "2"
   "Expected checks", "0", "0", "0", "15"

What is compulsory, and what the host could state instead:

* The Python step makes the arched deck, a generated mesh no typed kind makes;
  its description is the approval text. Compulsory.
* Numbers, argument names, operation kinds, names and aliases scale with the
  operations. Every footbridge plan (10 of 10) wrote one ``create_primitive``
  per post and per rail: six operations differing only in name and location,
  where one ``batch`` per unit states the unit once and its placement. The
  instructions already said to repeat with ``batch``, but Claude's plan named
  the kind without its schema, so using it cost a lookup round.
* Values equal to their default (``"Metallic":0``, ``"target":null``) are
  0.4-1.8% of a plan; a colour's alpha ``1`` is required by its schema. Not
  worth a contract change.
* Expected checks in long sessions state the request's goal (dimensions after
  a scale, a material on every face); the host cannot derive them, because the
  dimensions are the requested ones and "every face" depends on the slots an
  object already has. A restated new cube's dimensions (12 of 400 calls) is
  derived already.
* Thinking (25-30% of a build's output) is not attributable to the plan's
  form.

GPT final messages (strict format): a recipe call wrote what the strict form
required, the schema's widest fit-dimensions bounds, a tolerance,
``"recovery_observation":null`` and a revision the host infers; 108 of 108
recorded fit-dimensions calls stated exactly the widest bounds.

**Changes** (toolset 39; ``agent_integration.rst``):

* ``batch`` is exact in Claude's plan (``plan_kinds.CLAUDE_REPEAT_ACTIONS``),
  with its placement note as the field's description. The note now also says
  what copies are named and aliased and that the unit's material and modifiers
  go before the batch, the order ``program_rules.linked_edits`` enforces (one
  of five recorded GPT-6.1-Sol arched-footbridge turns lost a round to
  ``LINKED_EDIT``). Cost: 2,748 B of plan schema, read from the prompt cache.
* A Codex plan's three gate recipes may end after ``mode`` and stop their
  parameters where the recorded calls did, and a transform may stop after its
  scale (``strict_shapes``); fit-dimensions' scale bounds default to the
  widest a transform takes (``action_recipes.FIT_BOUNDS``), still shown in the
  approval and still refusing an out-of-bound correction. Cost: 1,707 B of
  output schema, cached with the prompt prefix.

**Offline replay.** The recorded plans re-encoded in the new form, the
model's other choices kept (``plan_output_accounting.py``; Claude: each
repeated unit as one create and one ``batch`` with every location as ``at``,
objects that only took the unit's material dropped from ``also``; Codex: each
final message encoded with today's output schema without what the old form
forced, assuming a recipe's ``tolerance`` and revision were not asked for):

.. csv-table:: Visible plan output per call, recorded -> re-encoded (means)
   :header: "Scenario", "Calls", "Fit tokens", "o200k", "Cut"

   "Opus arched footbridge", "5", "1,225 -> 1,003", "819 -> 685", "-222 fit (18%)"
   "Opus footbridge", "5", "829 -> 605", "527 -> 392", "-224 fit (27%)"
   "Opus arched footbridge, an earlier recording (its 3 program calls)", "3", "1,389 -> 1,183", "921 -> 796", "-206 fit (15%)"
   "GPT fit-dimensions call (Sol and Astra gates)", "91", "", "84.5 -> 44.4", "-40 o200k (48%)"
   "GPT material-setup call", "81", "", "91.5 -> 70.2", "-21 o200k (23%)"
   "GPT bevel-and-shade call", "25", "", "85.1 -> 68.2", "-17 o200k (20%)"
   "GPT gate or one-shot turn, every kind", "510", "", "65.8 -> 54.3", "-11.5 o200k (18%)"
   "GPT-6.1-Sol footbridge", "5", "", "519 -> 510", "-9 o200k (a transform's mode)"

A ``mirror`` or two-row ``array`` instead of ``at`` (``--best-batch``) cuts
about 40 fit tokens more per footbridge plan. Opus's lighting, cube and camera
plans and its long sessions do not change.
With ``mesh_bend`` among the deferred kinds the same recordings give the same
figures: ``plan_output_accounting.py --json`` writes byte-identical results
for the Opus runs above and for the recorded GPT gate and one-shot turns (683
final messages).

**Live confirmation** (one-shot tests, Angelo only; the machine's load average
was higher than in the recordings, so wall times are indicative):

.. csv-table:: Medians [least-most]; visible = output minus thinking
   :header: "Test, model", "Passed", "Output tokens", "Visible", "Thinking", "Seconds"

   "Footbridge, Opus 5.5, recorded", "5 of 5", "1,091 [1,066-1,192]", "861", "238", "11.0 [10.2-13.8]"
   "Footbridge, Opus 5.5, new forms", "3 of 3", "902 [867-954]", "668", "234", "9.5 [8.9-9.6]"
   "Arched footbridge, Opus 5.5, recorded", "5 of 5", "1,853 [1,723-2,013]", "1,344", "533", "19.3 [17.9-23.9]"
   "Arched footbridge, Opus 5.5, new forms", "5 of 5", "1,720 [1,531-2,097]", "1,211", "560", "18.3 [16.1-24.6]"
   "Arched footbridge, GPT-6.1-Sol, recorded", "5 of 5", "1,129 [1,117-1,428]", "", "", "40.3 [37.5-49.4]"
   "Arched footbridge, GPT-6.1-Sol, new forms", "3 of 3", "1,116 [1,040-1,202]", "", "", "33.4 [32.2-35.4]"

Every footbridge plan made the posts with one ``batch`` (``at``, the unit's
material before it) and two of three the rails too; the third named its rails
L and R and created both. On the arched footbridge four of five plans batched
the posts; the fifth built the whole bridge in one script. The program parts
fell by more than half (o200k 651 to 289 per plan, means), but three of five
plans now made the rails follow the arch in the script, so scripts grew (273
to 485), and thinking varies more than the cut (271-886 tokens). Footbridge output fell
17% (visible 22%) and its time about 1.5 s; the arched output fell 7%
(visible 10%).

The Sol arched footbridge makes its deck and rails in a script from
``exec`` and its posts in a program, so neither change shortens it much (its
recipe forms are not used there; its recorded row comes from an earlier build
that also lacked other changes). Every Sol program gave the post unit its
material and bevel before the ``batch``, the order the note now states; one
of five recorded turns had not and lost a round to ``LINKED_EDIT``. It took
4-5 model requests and no errors, against 5 in the recording.

**Not done, with reasons.** Shorter argument names would need a second name
per field (twice the schema) or rename every contract the plan, recipes,
Codex reference and recordings share. Parameters flattened into the
operation collide with kinds that take their own ``target``
(``constraint_add``). A ``mode`` that defaults to ``APPLY`` saves about 7 fit
tokens per program, too little to change what an omission means for a
mutation. A shorthand for a one-program plan (``{"program": [...]}``) would
save about 29 fit tokens per program plan (about 17% of a long-session edit
turn's output), but changes the plan's operation items, which the streaming
live row reads; it is left for that work. Numeric-precision guidance: the
recorded plans already write two or three decimals, except radians such as
``3.14159``.

Limits: offline figures keep the recorded model's other choices; whether a
model uses the new forms, and its thinking, only a live run shows.

Turn context v2
===============

Each sent message carries a bounded scene observation (``turn_context.py``;
``agent_execution.rst`` and ``scene_intelligence.rst``). It orders the active
object, objects the message names, selected objects and objects changed since
the last turn first, ahead of the remaining objects in scene order, and
supplies their exact ``object_revision`` values, so a first edit binds a
program without a discovery query (a whole model round for a Claude typed
plan). Bounds: 32 entries, revisions for at most 8 named, 8 selected and 8
recently changed objects plus the active one, every object of a scene with at
most 8 objects, and a payload of at most 16,384 characters.

Method: ``benchmark_blender_turn_context.py`` on the macOS arm64 Release build in
background mode, three runs, each value the median of seven calls after one
warmup; the table gives the median of the three run medians in milliseconds of
process CPU time, with the run range in brackets (wall time is not reported).
In every cohort the active object and eight selected objects are last in scene
order. The request-snapshot column is context only: the default Every Request
snapshot that the same send path already writes
(``save_as_mainfile(copy=True)``; `Request snapshot cost by policy`_), CPU
only, excluding disk waits.

.. csv-table:: Turn context build, process CPU per message
   :header: "Workload", "Turn context", "Revisions included", "Request snapshot (context)"

   "100 cubes", "2.1 [1.8–2.9]", "9 of 9", "4.8"
   "2,000 cubes", "4.4 [3.7–5.0]", "9 of 9", "8.4"
   "20,000 cubes", "26.4 [25.0–26.6]", "4–6 of 9 (others ``BUDGET``)", "71.7"
   "100 objects, active 99,856-vertex grid", "60.5 [59.5–61.4]", "1 of 9 (others ``BUDGET``)", "37.4"

Nearly all of the cost is exact revisions. At 20,000 objects each revision
costs about 5 ms, because ``object_revision`` checks scene membership and reads
``Object.users_collection``, both of which scan the scene; the cooperative
25 ms budget (``REVISION_BUDGET_SECONDS``) then stops further revisions, and
each omitted revision states its reason. The dense cohort is the single-object
worst case: the active object is always attempted first, and its revision of a
UV-mapped grid just inside the 100,000-vertex program input limit took 59.7 ms,
more than the snapshot the send already writes. A later discovery query for
that mesh would pay the same revision plus a model round, so the builder
accepts it; the budget is cooperative, not a frame-time bound. Payloads stayed
at about 6,400 characters.

Name matching (one ``keys()`` call and substring tests over at most 20,000
names) is within run-to-run noise. A message naming one unselected object adds
one revision (100 cubes: 1.6 → 1.8 ms), eight recently changed objects add
eight (100 cubes: 3.0 ms); the large cohorts gain few or none within the budget
(20,000 cubes: 0–2 of 8; the dense grid: 0 of 8).

**The budget counts the main thread's CPU time** (``work_clock.py``; one
reading costs about 0.4 µs, taken once per revision candidate), so a loaded
machine stops after the same work as an idle one. Under heavy load, 2,000
background builds reached the named object's budget check after a median
0.66 ms of wall time but a maximum of 40.4 ms (past a 25 ms wall budget), while
the main thread's CPU time never exceeded 1.9 ms.

**Change feed.** The feed adds work to the existing depsgraph handler only
while a chat has a feed for the scene being evaluated. One transform update
adds 0.03–0.47 ms. A relations rebuild, which reports every object, adds
0.36 ms at 100 objects, 2.8 ms at 2,000 (existing invalidation 3.2 ms) and
4.9 ms at 20,000, where reading stops at 4,096 updates and reports overflow
(existing invalidation 19.1 ms). Such rebuilds follow ID additions or
removals, including each staged program poll's private stage, whose rebuild
already costs far more (`Stage cleanup scans`_).

Limits: no provider, prompt-transfer or model-turn time is measured here. That
one round is saved rests on the program binding contract, proven in
``test_blender_turn_context.py`` by a production APPLY that used only
turn-context revisions.

Visual observations
===================

Method: ``test_blender_view_observation.py`` on the macOS arm64 Release build
in a factory-startup GUI, three runs without the guarded allocator. Each sample
is one ``angelo_view`` call for one 512 × 512 ISO view of two targets (the
default cube and a 32 × 16 UV sphere), with the image cache cleared first; five
samples per shading per run. Values are the tool's own timing fields: wall
time, and process CPU time, which includes every thread (EEVEE compilation, PNG
workers).

.. csv-table:: Per-run medians, milliseconds
   :header: "Capture", "Wall", "Process CPU"

   "SOLID, uncached", "9.3–11.0", "12.1–12.7"
   "MATERIAL, uncached, shaders already compiled", "67.8–70.5", "55.7–60.0"
   "Three cached views (two exact target revisions, three file writes)", "2.4–2.7", "2.4–2.7"

Most SOLID time is drawing and GPU readback (7–8 ms); waiting for the encoder
adds under 1 ms. Warm MATERIAL time is EEVEE rendering the scene's viewport
sample count (16 by default) in one call; waiting for the encoder adds about
3.5 ms. The first MATERIAL capture in a process also initializes EEVEE and
compiles the new material synchronously, blocking Blender's UI: 3,988 ms wall
and 979 ms process CPU with a cold operating-system shader cache, and 95–104 ms
wall (183–204 ms CPU) once that cache held the same shaders. Each additional
uncompiled material adds its own compilation, so a scene with many new
materials can block for many seconds. The first SOLID call of a process (three
views) took 35–44 ms.

PNG encoding (``view_png.py``) uses zlib level 1 with filter 0 and drops alpha.
Level 6 took about six times as long for a 512-pixel material view (about 30
against 5 ms) for about 35% fewer bytes; image token cost depends on pixel
size, not PNG bytes, so level 1 is used.

Limits: a two-object scene. Draw cost grows with what the viewport draws, and
it runs on Blender's main thread. These are tool timings, not provider latency
or model image processing. Windows, Linux and other GPUs were not measured.

Free cameras
============

``angelo_view``'s free camera and reference pairs (``agent_execution.rst``,
"Free cameras and reference pictures") let a model look from a reference
picture's viewpoint in one call. In recorded debug-mode one-shot trials, the
models' remarks often said that a reference picture's viewpoint was not
checked or was guessed.

**What they would have saved, measured.** Every tool call of 81 recorded
debug-mode Angelo trials (every model and arm) was read, and the calls that
create, aim or delete a camera, swap the scene camera or move the viewport
were labelled by hand. A call counts as look-only when it exists only to see
from a viewpoint other than the brief's camera or an axis view; the brief's
own camera, its retries and final renders do not count.

* No trial had a look-only camera, script or round, and none an approval,
  since the trials ran Auto with scripts always allowed. Nothing is saved.
* In its 48 bridge trials the front reference was compared in 45 and the top
  in 32, through axis views; the two free-viewpoint references (side from
  upstream, hero) in none.
* The tree brief's one reference has no viewpoint: nothing to gain there.

So the return is visibility, not saved work: the change costs a call (usually
no extra round: 45 of 48 bridge trials already had a round with a view to put
it in) where a model chooses to compare, and 0.33-0.36 s for four LIT pairs on
a recorded bridge scene. A model whose only way to look is a temporary camera
spends calls and rounds on it. Small samples; whether a model will compare
once it can is not measured (no model was run for this change).

**What they cost.** Model-visible bytes, read from the prompt cache:
Claude's plan schema 54,822 to 55,424 B (+602: the item's schema and the
``views`` note, ``view_spec.VIEWS_NOTE``), every tool declared in full 101,648
to 102,259 B and an OpenAI thread's tools 75,450 to 76,061 B (+611), and each
runtime's instructions +11 B (the view guidance names the free camera).
A pair's picture is twice a view's pixels.

**Replayed on recorded scenes.** A replay in the compiled app ran the brief's
four reference cameras as free cameras on three recorded bridge scenes.
Against a camera object at the same values with the same clip range, every
picture matched to 0.015 levels or less (LIT and SOLID); with a 0.1 to 100,000
range on the camera object the orthographic front and top views differed by
up to 3.0 levels, the depth precision of that range, not framing. Against the
harness's own EEVEE renders of each trial from those viewpoints, grayscale
correlation was 0.997-0.999, against 0.48-0.95 with the camera moved sideways
by a tenth of its distance and 0.42-0.84 at 1.5 times its lens. On four held-out
scene types the work was not developed on (tree, cottage, castle, the heavy
scene), a diagonal and a straight-down camera derived from each scene's bounds
matched camera objects with the same clip range to 0.026 levels or less, the
scene camera given as a free camera matched SCENE_CAMERA (its own clip range)
to 0.061, and a pair whose reference was the SCENE_CAMERA view had halves
within 0.014 levels.

Reference comparisons
=====================

A pair's ``compare`` (``OVERLAY``, ``OUTLINE``) and the ``MATCAP``,
``CURVATURE`` and ``DEPTH`` looks (``agent_execution.rst``, "Free cameras and reference
pictures" and "Sculpt looks") are the face and hair aids: the face and
hair tests' 2 mm tolerance is 5 pixels at their framing (``oneshot_tests.rst``,
"Grader uncertainty"), which two pictures side by side leave to the model's eye.

**What they cost.** Model-visible bytes: Claude's plan schema 55,797 to 55,975
B (+178: the ``compare`` enum, three shading names and nine words of the
``views`` note), every tool declared in full 102,167 to 102,550 B (+383, with
the description's sentences on the looks); instructions unchanged. On the head
fixture of ``test_blender_view_outline`` (M-series Mac), one 1,024-pixel pair
took 34-50 ms and the same camera as ``OUTLINE`` 100-152 ms: the silhouette
and its depth drawn at twice the size, the subject's regions, the outline
distances and the gaps, on the PNG worker thread except the draw. Memory is
bounded by the comparison's chunks (2 MB arrays) and by comparing outlines of
more than 8,000 edge pixels at half size: a dense-outline case (49,000 edge
pixels a side at 1,024) takes 92 MB and 0.05 s, against 1,546 MB and 1.09 s at
full size. An ``OVERLAY`` or ``OUTLINE`` picture has a view's pixels, half a
pair's.

**Checked against Blender.** A head drawn through a free camera and compared
with itself by ``OUTLINE``: 95th-percentile distance 1.0 pixel, centre offset
0.1 pixel or less, orthographic and perspective. A 0.03 m move gave 25.69
pixels where ``world_to_camera_view`` gives 25.6 (orthographic) and 19.04
where it gives 18.96 (perspective); a 1.05 scale gave 1.047 and 1.046. Every
gap's surface point lay within 0.004 mm of the moved head (Blender's own
closest-point query) and its offset pointed back along the move. A
hair object's silhouette held all 126,000 projected strand points within a
pixel. Compared with Blender's own picture of it, by ``OUTLINE``: a torus
through its hole IoU 0.997 (0.992 as a cut-out), Geometry Nodes instances
0.989, dense hair 0.996, sparse hair 0.978, a comb beside hair 0.982, each
within a pixel at the 95th percentile, and legacy particle hair 0.94-0.96
within 1.0-3.6 pixels over six seeds. ``DEPTH``'s nearest
distance was Blender's ray cast to 0.15 mm. On the 79 distinct reference pictures of the
one-shot tests, which this work was not developed on, the background was read
as plain on all 22 face and hair sheets and 19 of the 20 small-scene sheets
(their outlines follow the heads and the hair mass), and on 15 of the 37
larger-scene pictures, the orthographic sheets; every close-up, hero view and
photo read as busy, so an outline there is stated as a guess.

**Measured with models** (face and hair tests, diagnostic runs with debug
thinking and scripts always allowed; the same briefs, references and graders
in each arm). GPT 6.1 Sol, effort low, three trials an arm, interleaved: every
views trial used free-camera reference pairs (15-27 a trial) and none used
``OVERLAY``, ``OUTLINE`` or a sculpt look. On head-portrait the front
outline's 95th percentile went from 6.8, 6.5 and 5.1 mm without the aid to
3.7, 2.9 and 3.1 mm; the side outline, profile landmarks and eyes did not
change, no trial passed, and checks passed were 6, 9, 8 against 9, 8, 8 of 15.
On hair-short nothing changed (8, 9, 8 against 8, 8, 9 of 11). Median wall
94 against 99 s on the portrait and 106 against 88 s on hair. Opus 5.5, one
trial an arm: it compared by ``OVERLAY`` (12 and 18 times), stopped early at
effort low and changed nothing measurable.

**A pair with targets states the outline** (the same conditions, Sol, three
trials a test). The records reached the model (34-52 outline records a
portrait trial) and it acted on them: head-portrait front outline 2.7, 2.0,
1.9 mm and side 5.1, 3.6, 4.0 mm (views alone 3.7, 2.9, 3.1 and 3.7, 5.0,
5.2); hair-short hairline 3.1, 4.9, 3.5 mm (views 11.6, 14.2, 5.9) and checks
9, 9, 10 of 11 (views 8, 9, 8). No trial passed. On two tests no aid was made
on, interleaved with the views arm, two trials each: head-stylised side outline
2.5 mm against 7.8 and 8.8 (9 of 15 each; the second pair-facts trial ended on
the provider's "model at capacity" after 24 s, counted as a failure);
hair-long 7 and 8 of 11 against 9 (the second views trial ended the same way
after 4 s), hairline 11.7 and 4.0 mm against 3.0. So the faces' outlines
improve on every face test; hair does not, measurably. Cost: the always-sent
prefix is unchanged; a pair's result grows by about 1 KB of record (its gaps
most of it) and the field's 546 B description once a result; each such pair
draws the targets' silhouette (+33 ms a pair in the recorded calls, 0.17 to
0.20 s). The model works longer with the numbers: portrait median wall 169
against 94 s (input tokens 2-4x, from more rounds); hair 110 against 106 s.

Fewer model rounds per edit
===========================

A live Claude run of the latency harness (`Live-agent latency harness`_; low
effort, one sample per task) showed model rounds of about 3–5 s each against
local execution of milliseconds (the longest main-thread block was 81 ms). An
edit took two to four typed-plan rounds; bevel-and-smooth took four (26.4 s).
Its transcripts show three avoidable kinds of round:

* a lookup round before every edit of an object the agent had created: it was
  never active or selected, so the turn context carried no revision for it, and
  the previous program's result gave no post-commit revision;
* a summary round after every edit: each plan set ``verify=true`` although its
  expected checks had already passed, so the host re-sent
  ``angelo_inspect_scene`` and Claude only summarized;
* a read-only expansion round before a recipe's program round.

The contracts that remove them, each described in its own document:

* Program results carry post-commit ``live_revisions`` (``agent_execution.rst``),
  and the turn context revises objects the message names and objects changed
  since the last turn, including the agent's own commits
  (``scene_intelligence.rst``). Either removes the lookup round.
* A ``verify=false`` plan whose every edit is proved by passing expected checks
  ends with a host-composed summary of the actual read-back
  (``agent_integration.rst``, "One model round per edit"). Continuation rounds
  that remain send the bounded turn context instead of the full
  ``angelo_inspect_scene``.
* ``angelo_apply_recipe`` expands and stages a recipe in one approved call
  (``local_verification_and_recipes.rst``).

When Claude follows the instructions each such edit takes one round; a later
five-trial live run of the harness's five tasks took one model call per task.
Whether the model writes goal-stating checks and sets ``verify=false`` is
natural-language behaviour that only a live run shows; the contracts are
proven by ``test_native_round_savings.py``, ``test_blender_turn_context.py``
and ``test_blender_agent_workspace_ui.py``. Codex's internal rounds are not
observable; it gains the same post-commit revisions, turn-context rows and
one-call recipes as fewer tool calls.

Local cost: post-commit revisions add about one ``object_revision`` per written
object, 4–7% of a staged program's retired instructions
(``benchmark_blender_agent_execution.py``: a one-operation program about 1.07
billion instructions; the benchmark's target revision 61–65 million, 2.8–3.3 ms
CPU). CPU-time medians of the same operations stay within run-to-run noise. The
extra revisions of named and recently changed objects are measured in
`Turn context v2`_.

One-shot rounds
===============

Every extra model request of Angelo's one-shot turns is read from the
receipts of the one-shot tests (``oneshot_tests.rst``; provider requests and
the path's steps, ``request_path.py``) and from each trial's own chat (its tool
calls in order and the model's reasoning summaries). Codex reports model
requests, not rounds; in a Codex plan thread every ``exec`` call is one request
and the final plan one more (``gpt_launch.rst``, "Codex typed plans").

The causes found in recorded one-shot turns (five trials per test, effort low):

.. list-table:: Extra requests on the one-shot minis
   :header-rows: 1

   * - Test, model
     - Requests
     - Each extra request and its cause
   * - Arched footbridge, GPT-6.1-Sol
     - 5
     - (1) ``angelo_blender_api`` for ``Mesh.from_pydata``, which the instructions already state; every
       reasoning summary cites the instruction to look an API up before unsupported Python. (2) The arch as
       a reviewed script: no typed kind bowed a mesh, and a Codex plan cannot end with Python
       (``codex_plan.IN_TURN_ONLY``). (3) The posts, material and bevels as an in-turn program, so that (4)
       ``angelo_view`` could look at the scripted shape, which no read-back stated. Every turn replied
       straight after the view: none of the looks changed anything.
   * - Arched footbridge, Opus 5.5
     - 1 (one trial of ten: 2)
     - A script rides in Claude's plan beside the program, without a round. The one extra round corrected
       a script that set a read-only attribute it invented.
   * - Cube (bevel, subdivision, brushed copper), GPT-6.1-Sol
     - 2
     - An ``ACTIONS`` lookup of ``shader_nodes`` for a noise-bump texture; Opus used
       ``material_principled`` in one request. Both pass.
   * - Flat footbridge, camera, lighting: Sol and Opus
     - 1
     - None.

Other causes, found in earlier recordings: ``transform_apply`` lookups
(`Everyday kinds`_), a material named across a script and a program
(``material_rules.not_in_program``) and the scene camera's pointer form
(``agent_integration.rst``, "Toolset 39"). Outside the one-shots no recorded
turn looked at its own result unasked: the survey's two view tasks, which ask
for a look, took one view each on every model.

Changes, each the general form of a cause above:

* ``mesh_bend`` (``native_coverage.rst``, "Mesh editing") bows a whole mesh in
  a program, the ends and length kept. The Codex program reference states it
  with a line of what its numbers mean (``action_schemas.BEND_NOTE``), so the
  arched footbridge is one plan; ``test_blender_mesh_bend`` builds it with one
  program and passes the test's five closed checks. Claude's plan names it
  only (+12 B; `Claude's plan states mesh_bend`_). Sections stay upright as
  they bend: bent square to a circular arc, they pushed a 4 m deck's top
  corners out to 4.05 m, and a model spent a round fitting the deck back to
  4 m.
* The API-lookup sentence asks for a lookup only of an API the model is unsure
  of and says ``from_pydata`` needs none (+6 B, every runtime).
* A Codex chat thread's tool guidance says when to look at one's own edit: when
  the person asks how it looks, asks for a picture or gave one to match, or when
  a script made its shape (+328 B). With ``mesh_bend`` and without the sentence
  GPT-6 Astra still ran the program in-turn and looked at it in 3 of 4 trials,
  two more requests each; no look led to a change but the 4 m fit above, which
  the dimensions already showed. Claude's instructions are unchanged: Opus never
  looked unasked.

Cost: +723 B of instructions and +12 B of output schema on every Codex plan
thread request (``codex_prompt_probe.py --variant plan --task edit``, unbilled:
a thread's first request 105,112 to 105,863 B), about 200 tokens read from the
cache after a thread's first request. Claude's prefix grows 18 B
(``agent_integration.rst``, "Toolset 39", lists every part).

Confirmation (``bench_mcp_vs_angelo.py --sides angelo``, effort low,
``--angelo-trusted-scripts``, input isolation on; two or three trials a cell on
a machine shared with other work, so latency compares only within a row): the
arched footbridge took one request per trial on GPT-6.1-Sol (3 of 3 passed,
23.3 s [22.0-24.0] against 40.3 s [37.5-49.4] with five requests before) and
on GPT-6 Astra (2 of 2, 22.7 s; three requests and 29.9 s without the look
sentence); Opus kept one request with a script in its plan; the cube kept its
``shader_nodes`` lookup (`Deferred lookups in the recorded runs`_). One
GPT-6.1-Sol trial of six spent a correction round on a host rejection
unrelated to the bend: its plan edited the post after a linked ``batch`` had
copied it (``LINKED_EDIT``).

Claude's plan states mesh_bend
------------------------------

Named only in Claude's plan, ``mesh_bend`` goes unused: Opus does not look a
named kind up, and in 26 of 28 recorded arched-footbridge turns it made the
arch with a reviewed script inside the plan (the other two joined straight
parts), about 390 of a plan's output tokens (`Plan output tokens`_). Stated
exactly, with ``BEND_NOTE`` as its parameters' description, as ``batch``
carries its placement note (``plan_schema.core_operation``), the kind costs
709 B of plan schema (54,455 to 55,164 B: the kind's schema and the 272-byte
note in place of its name), about 346 input tokens a request, read from the
prompt cache. Every Codex surface, Claude's instructions, every tool declared
in full and the action index are unchanged by it, byte for byte.

An A/B of the two forms: the arched-footbridge one-shot, Opus 5.5, effort low,
five trials a side interleaved one trial at a time, each side's first trial
after its prefix change left out as the warm-up. The harness loads the add-on
from the checkout it runs from (``eval_live_agent_session.py`` puts its
``scripts/addons_core`` first on ``sys.path``), not from the
``--angelo-binary`` bundle, so each side ran from a checkout of its own code.

.. csv-table:: Five trials a side, medians [least-most]
   :header: "Plan", "Passed", "Seconds", "Output tokens", "Thinking", "Arch"

   "``mesh_bend`` named only", "5 of 5", "21.0 [16.9-25.1]", "1,997 [1,661-2,023]", "592", "script in 4, straight parts in 1"
   "``mesh_bend`` stated", "5 of 5", "13.0 [11.9-20.1]", "1,387 [1,307-1,444]", "319", "``mesh_bend`` on deck and rails in 5"

Every trial took one model request and passed the test's five closed checks.
Stated, every plan bent the deck and both rails with ``mesh_bend`` (rise 0.2
or 0.25 m along X) and wrote no script; the slowest stated trial (20.1 s)
spent 18.7 s in one API call of 1,307 output tokens, a provider delay.
Claude's plan names the kind only all the same (`Concepts, not scenes`_): the
A/B measured one brief's arch, while the 709 B would ride on every Claude
turn.

Deferred lookups in the recorded runs
-------------------------------------

Every capability lookup in the receipts of 3,205 recorded benchmark tasks
(gate sets, one-shot tests and their re-checks):

.. list-table:: Lookups by family and kind
   :header-rows: 1

   * - Lookup
     - Sol, Astra, Opus
     - When
   * - ``ACTIONS`` ``transform_apply``
     - 18, 10, 1
     - the ``bevel_and_shade`` and ``bevel_03`` gate tasks, before the Codex reference stated the kind;
       none since
   * - ``RECIPES`` index
     - 21, 2, 1
     - the same bevel tasks, before the recipe's ``apply_scale``; none since
   * - ``ACTIONS`` ``shader_nodes``
     - 6, 0, 0
     - the cube one-shot
   * - ``TOOLS`` (sculpt, Studio, Flow, jobs, memory, recipe expansion, scene settings, ``.blend`` hygiene)
     - 0, 0, 0
     - never

In the 1,643 most recent of those tasks, Opus made 0 lookups in 852 tasks,
Astra 0 in 378 and Sol 5 in 413 (all ``shader_nodes``). A fit of turn time
over those receipts gives about 1.9 s per extra request plus 26 ms per output
token on Sol (13 ms on Astra, 9 ms and 1.3 s on Opus), so a lookup round costs
about 3.5 s.

Stating ``shader_nodes`` in the Codex reference would add 1,685 B (about 510
tokens) to every Codex chat request. Over those tasks' 829 Codex requests that
is more input than the five lookups it would save (each reads the cached
prefix again and adds the 10.4 KB schema uncached), for about 17 s of latency
in all, and no other family was looked up, so the split is unchanged. The data
come from benchmark task sets; a family real sessions use often would show
here only through recorded sessions of those uses.

The castle one-shot's first plan
================================

On a large scripted build the first plan's time is mostly thinking. On the
castle one-shot (Opus 5.5, effort low, five trials) Angelo's first plan
thought for 4,553-8,753 tokens (median 7,202; 51-91 s), then wrote a script of
about 7 KB in about 33 s. Opus's summarized thinking (a replay with
``--thinking-display summarized``) shows what the tokens are: the castle
designed and much of its code drafted in thinking before it is written again
as the plan's script. The tokens cost twice: as output, and as the next
request's cache write (the plan's thinking blocks stay in the conversation).
Everything else was small: the plan also looked ``angelo_job_start``'s schema
up (a round), and in two of five trials the closing summary came as plain
text, so Claude Code forced a second ``StructuredOutput`` request.

Replays of the recorded first request, with only the appended instructions
varied, found no single block of the instructions that explains the thinking;
an explicit sentence capping thinking before a script about halved it on this
scene. Claude's instructions set no such cap and leave the way of working to
the model (``agent_integration.rst``, "Toolset 39"): a cap measured on one
scene is not a fix for a class of requests (`Concepts, not scenes`_).

Thinking summaries
------------------

Claude Code 2.1.301's ``--thinking-display summarized`` streams summarized
thinking text in print mode, the first about 2.4 s after the request starts
(the ``showThinkingSummaries`` setting is ignored there); ``claude_stream``
already draws thinking text as a thinking row. Without it Claude Code asks the
API for brief "updates": long thinks arrive with no text at all (the empty
"Thought for 130s" rows a user reported), while small thinking updates keep
streaming, so silence is never a sign of a dead connection (no keep-alive pings
were seen).

Measured on 2026-10-21 (pre-registered plan, thinking-ux 1.0.4; Claude Code
2.1.306, Opus 5.5 effort low, Claude subscription, three-message sessions,
8 + 8 interleaved trials and 4 + 4 sessions switching display mid-way; labelled
"Mac 1 with the desktop app open", load 2.5-4.8 at send, five trials over 5.0
set aside and re-run once):

.. list-table::
   :header-rows: 1

   * - Metric
     - Updates (1.0.3)
     - Summarized
   * - Cache-read share, calls 2+ (median)
     - 97.7%
     - 97.8%
   * - Session switched to summarized: first read share (median)
     - 97.3% (stayed on updates)
     - 97.1%
   * - List $ per trial (median, range)
     - $0.1218 (0.115-0.128)
     - $0.1207 (0.113-0.128)
   * - Output tokens per trial (median)
     - 1,996
     - 1,952
   * - Wall per call (median / p75)
     - 7.09 / 10.13 s
     - 6.69 / 9.95 s
   * - First thinking text (median)
     - none shown in any call
     - 4.1 s

The request prefix is byte-identical either way; only ``thinking.display`` and
the beta header ``thinking-display-updates-2026-08-18`` (sent with "updates")
differ. Summaries therefore cost nothing measurable and leave caching intact; long
(60 s+) thinks were not in the sample. Codex already requests summaries
("auto"); "detailed" summarized no more items and is not used. Diagnostic runs
use the display as debug mode (``debug_mode.rst``).

Streamed progress
=================

What a person sees before a turn's first step, on Claude: the live row's
"Thinking" while the model thinks (no text: Claude Code omits thinking unless
asked), then, from the plan call's start, its provisional steps
(``plan_steps``) and, for a script step, the script's code as it is written
(``script_stream``, ``script_overlay``; ``agent_chat.rst``). On the recorded
Opus 5.5 bridge one-shots of the 1.0.0 scene campaign the model thought for
~50 s (median) and wrote its script for ~38 s; Blender + MCP showed its first
tool call (a lookup) at ~3 s, and its first Python call started after ~22 s of
thinking and was complete at ~57 s (block times from the transcripts).

Measured by what the chat draws (``eval_receipts.ProgressWatch``, polled every
20 ms): ``first_plan_row_ms`` (a provisional step, a script line, or a plan or
edit call's row), ``first_script_ms``, ``first_progress_ms`` and
``longest_still_ms`` (the longest stretch with nothing new on screen before the
first step). Blender + MCP's counterpart of the first plan row is the start of
its first ``execute_blender_code`` call (``bench_mcp_report.script_call``).
``test_blender_script_stream`` times the main thread while code streams
(event-loop tick gaps, full redraws); with ``ANGELO_SCRIPT_STREAM_TIMING=1`` it
times a build without the well on the same stream, for an A/B.

Summarized thinking is off by default in 1.0.4 (``debug_thinking.SHOWN_TO_EVERYONE``; maintainer 2026-10-22: "Off
for 1.0.4", after the whole-task Opus A/B measured +11-14% cost and +8-10% wall). It had been turned on (maintainer
2026-10-21: "Yes, if it costs nothing") on the evidence below, from short sessions. A pre-registered probe on
Opus 5.5 at low effort (short three-message sessions, 8 + 8 interleaved and
4 + 4 switching between the displays) found: calls-2+ cache read share 97.8%
against 97.7% with thinking omitted, 97.1% against 97.3% across a switch; list
cost $0.1207 against $0.1218 per trial; per-call wall 6.69 against 7.09 s
median; the first thinking text after a median 4.1 s, against none.

Not yet measured on whole tasks: the pre-registered plan compares the build
before the script well, the shipping build (the well and summarized thinking),
and that build with the display off as a control, with Blender + MCP, at
real-user settings. The well ships only with a measured return (a shorter
still stretch) and no wall-time regression; if long thinks change the
display's cost or caching, it goes back to the maintainer.

Castle one-shot misses
======================

Every castle miss of the recorded script-kit runs (saved files re-graded, the
hero, top and gate renders looked at), the line of the model's script that
made it and why its own check let it pass:

.. list-table::
   :header-rows: 1

   * - Miss
     - Failed checks
     - Mechanism
     - The script's own check
     - Class
   * - 1
     - an open round-arched gateway (crown 3.50 m)
     - the arch cutter, a cylinder made by swapping two axes, is mirrored and
       wound inward (signed volume -125 m³ against +125); the EXACT
       difference removed only the box below the springing line
     - none asked for
     - geometry error, no verification
   * - 2
     - a gatehouse 10 m wide across the south wall
     - the gatehouse placed from the wall's outer face into the courtyard
       (y -16 to -9) instead of across the wall line y = -15
     - none asked for
     - placement misread, no verification
   * - 3
     - the gateway, closed by the gatehouse
     - a cutter profile listed the wrong way round: the cut did nothing
     - counted the gatehouse's faces (10) with no target
     - geometry error, weak verification
   * - 4
     - an open round-arched gateway (crown 3.50 m)
     - a mirrored cylinder cutter again
     - one ray at 2 m, below the springing line
     - geometry error, weak verification
   * - 5
     - the curtain wall's bounds; nothing floats
     - the east and west merlon runs laid over y -20.3 to 20.3, past the
       32 m wall and outside the corner towers
     - measured the arch's crown, not the walls or merlons
     - geometry error (span), missing verification
   * - 6
     - the enclosure; thirty merlons; nothing floats
     - the curtain wall built as one mesh of four overlapping boxes and cut
       with the EXACT solver without self-intersection handling: three of
       the four walls were deleted (reproduced: 2,520 to 715 m³)
     - measured the arch's crown only
     - boolean misuse, missing verification

No miss was a script error, a partial build or the harness's.

**What removes each class.** ``kit.cut`` and ``kit.arch`` wind every cutter
outward, handle a target of overlapping parts, and refuse (changing nothing) a
cut that misses or that would take the target's extent where the cutter does
not reach; ``kit.row`` keeps a run inside the span it is given; a span given
wrong is caught by ``kit.check_supported`` (it names the floating parts) and a
misplaced part by ``kit.check_bounds``. A failing check is information: one
result round, and the person reads the checks still open ("Tools aid, never
handicap"; ``agent_execution.rst``, "The script kit and self-checks").

**A refused still start.** A bridge brief asks for frame 60. A plan that knows
only a still's ``kind`` and ``output`` guesses ``frame`` (refused: unknown),
then ``frame_start`` with ``frame_end`` (refused: a RENDER_STILL takes no
``frame_end``), then ``frame_start``: two cached rounds, 6-14 s a trial, in 11
of 15 recorded bridge trials. The host reads both as the still's one frame
(``job_spec.still_frame``; ``long_jobs.rst``, "The tools"): ``frame`` alone is
``frame_start``, and a ``frame_end`` equal to it is dropped; every other frame
field or value is refused as before.

Colours a request states
========================

A bridge brief asks for "a matching hazy sky (a physical sky or a warm sky
colour)". Across 51 recorded bridge trials (saved scenes inspected), every
Physical Sky texture passed the golden-sky check, and every miss took the
colour option and wrote a near-grey scene-linear background that the model
believed warm: (0.55, 0.5, 0.55), (0.62, 0.6, 0.68) and (0.55, 0.5, 0.52)
twice, on-screen saturation 0.04 to 0.05. Three of the five took the colour
after looking at a RENDERED preview and "warming" a blue one. Thinking did not
separate the trials that skipped the Sky texture (median 2.7k tokens against
2.3k). A kit colour check built for this class went unused and was removed
(`Concepts, not scenes`_).

The parenting class: two of 38 recorded bridges left a car's parts behind. One
built the parts at world positions and gave them
``matrix_parent_inverse = body.matrix_world.inverted()`` right after setting
the body's location, before any evaluation, so the identity inverse moved
every part by the body's offset; the other left the wheels unparented. The kit
parents in place (``kit.parent``: evaluate, parent, inverse from the fresh
matrix, child back at its world matrix), and before each such statement in a
reviewed script the executor evaluates the view layer when that parent's
matrix is stale (``script_prepare``; the result names the lines,
``evaluated_before_parent_inverse``).

Concepts, not scenes
====================

A fix solves a class of requests, never one scene. Code defaults, vocabularies
and sentences shaped by the benchmark briefs rather than by a class of
requests are removed; the briefs are named here only as evidence.

* **A row's sizes are the script's.** ``kit.row`` defaulted its unit and gap to
  one brief's battlement sizes, so a script that left them out silently got
  that brief's units. Both are required (``agent_execution.rst``, "The script
  kit and self-checks").
* **No colour check.** ``kit.check_colour`` was offered in 24 recorded builds
  and called in none, while its vocabulary grew an exception for one brief's
  wording after another (lime plaster, golden hour, hazy, warm grey stone,
  floors fitted to the recorded greys). It is removed with everything only it
  used: its colour words, the node-graph reader, the Sky table and its
  measuring script, its tests, and the gate's colour-only closing (an
  unmeasured colour check closed an open one), so only a pass closes a check.
* **No arch kind for one brief.** ``mesh_bend`` stated in Claude's plan would
  put 709 B of plan schema on every Claude turn for one brief's arched deck
  (`Claude's plan states mesh_bend`_). Claude's plan names it only
  (``plan_kinds.CODEX_REFERENCE_ACTIONS``): a lookup returns its schema and
  note, and a Claude plan can also write an arch as a script in the same plan,
  without a round. The Codex reference states it (its plan cannot end with a
  script). Claude's plan schema is 54,455 B without it.
* **No cap on thinking.** A cap on thinking before a script was measured on one
  scene alone. Thinking longer is fine unless it is shown to buy nothing, so
  the instructions set no cap.

Support through the scene
=========================

**The miss.** A recorded bridge build checked its car with
``kit.check_supported('car', 'Car Wheel')``. The wheels sat on the deck 1 m up, the deck on abutments in the
valley, and the check read all four as floating: a part counted only on the
ground plane or through the group's own parts, and its row named neither the
ground height nor what was below. The model sank the wheels 2 cm, raised them
2 mm and unparented the car, and ran out of correction rounds without its
still. Replaying its five scripts in a background stock Blender reproduces it:
the earlier search fails ``car`` in all four that check it, the current one
passes it from the first, whose only other failure (the valley 5.6 m high against 5.5) the
model fixed in its next round.

**The class.** Anything resting on an object outside the checked prefix: a
car on a deck, a cup on a table, a lamp on a desk, a roof on its tower, a flag
on a summit, trees and rocks on a terrain. In the repository's own fixtures
the castle stand-in's ``Castle Tower NE Roof`` and its 70 windows set in the
walls, and the heavy scene's flag, 544 tree parts, 441 rock parts, watchtower
roof and statue, each checked alone, all "floated".

**A chain, not one contact.** Counting any touched surface as support would
pass the recorded call whatever the car stood on, since the wheels touch the
body. So a part is supported when a chain of contacts joins it to the ground:
through the group's parts as before, then outward through any other visible,
rendered, solid-drawn object or instance, one ring at a time, until every
floating component reaches something grounded or nothing new touches it
(``agent_execution.rst``, "The script kit and self-checks"). Contacts come
from surfaces, never an object's bounds: a terrain's or a room's bounds would
hold everything inside them, so a lamp floating in a room or a rock over a
terrain would pass. A failing row states the support looked for (``ground=``,
``tolerance=``) and, per floating part, lowest first, the nearest surface
below, the gap and the lowest point its contacts reach.

**Unchanged where it passed.** Parts on the ground or on each other are
recorded exactly as before, byte for byte, without reading the scene; the
search only adds support, so no check the earlier search passed can fail.

**Held out.** Neither fixture was built for this: the castle stand-in with
three variants and the heavy scene with four, 36 checks. Every check the
earlier search passed passes with the same entry; the seven prefixes above now pass;
every real defect still fails and names its gap: a roof 2 m above its tower
(gap 2.0 to the tower), the flag 1 m above the summit (1.0 to the terrain), the
watchtower roof left 12 m up, the half-height keep's windows. The flag sunk
0.5 m into the summit passes. The new cases (``test_blender_script_kit_support``)
are unrelated scenes: a car on a deck on piers, a cup on a table, a shelf on a
wall with a stack of books, a tabletop on another group's legs, a crate across
a log, a frame on a wall, instanced and Geometry Nodes supports, and parts
that float (in the air, in a room, over a terrain, a car in the air checked by
its wheels, a car on a deck with no piers).

**Cost.** Background stock Blender 5.2 at ``nice 19``, one run each: the castle's checks take 0.012 s at most; in the heavy scene (a
692,224-vertex terrain, 1,969 objects and instances) a check that reaches the
terrain takes 0.15 s and the 300 trees 0.47 s, against 0.001 to 0.36 s for the
earlier search's false failures; a car on a deck among 90,601 Geometry Nodes instances
takes 0.31 s, and 2,000 stones floating over a 245,000-triangle field 0.79 s.
Most of the terrain's cost is Blender triangulating it, which a viewport has
usually done already. Reading positions in Blender's own float32, then
widening, halved that read for every kit check (same values). Every search is
bounded: the vertices and objects it reads count in the check's two million
vertices and 20,000 parts, it lists at most 250,000 objects and instances, and
a contact test stops past 20 million comparisons; past any, the check is
unmeasured, never passed.

**Limits.** A part's bounds stand for it, as within the group, so a concave
part (an arch, a ring wall) whose bounds hold geometry it does not touch
counts as touching it. A ground that is a mesh wholly above ``ground`` (a
plateau) holds nothing up until the check is given its height; the row's
``reaches_down_to`` shows the height its contacts reach. A part wholly inside a
closed solid, touching none of its surface, floats. A failing row names
``ground`` and ``tolerance`` when they matter.

Boolean results
===============

**What it adds.** A completed script's ``booleans`` rows: what each Boolean it
applied, or left live, did to its object's faces, world volume and bounds,
with a plain fact at the extremes only (``agent_execution.rst``, "What its
Booleans did"). Evidence: class BOOLEAN of a mistake catalogue of 1,106
one-shot trials (34 events, 8 final misses), where the script's own result
gave a face count and no volume.

**Method.** A release build in background at ``nice 19``, with an isolated
profile: ``test_blender_script_booleans.py``,
``cost_and_bytes``. A scene of 300 unit boxes; one script adds four walls with
door Booleans, applies three, leaves one live and moves one box. Seven runs
with the hook and seven with it replaced by a no-op, interleaved, each on a
rebuilt scene; the wall time of ``BlenderExecutor.execute``, median. The
hook's own code is timed apart (``perf_counter`` around every ``measure`` and
around the whole live reading).

**Timings.** Inside the hook, the stable figure: 1.1 to 1.8 ms for the twelve
measures (before, operand and after of three applies; base, evaluated mesh
and operand of one live stack) and 0.4 to 0.6 ms for the whole live reading,
in three runs; a cutter is small, so its measure adds little. End to end,
49.3 against 46.7 ms and 48.8 against 46.6 (+2.6 and +2.2 ms; in stock Blender
5.2, +1.2), with runs spread about 3 to 5 ms either way; under heavy load
(load average 29) the same comparison spread over 80 to 176 ms and measured
nothing. ``measure`` alone (stock Blender 5.2, best of five): 0.05 ms for a cube, 0.8 ms at 9,800
triangles, 6.9 ms at 89,400, 18.5 ms at 249,000; past 250,000 triangles it
reads the face count only. Reading the mesh's own attributes
(``.corner_vert``, ``.corner_edge``, ``.edge_verts``, ``position``) instead of
``loops``/``edges`` RNA took each read of a 180,000-corner mesh from 5 to
11 ms to under 0.05 ms. The search for a target whose operand the script changed reads
one modifier list per object: 1.1 ms on 5,000 objects (one in ten with a
modifier), 2.6 ms before objects without modifiers were skipped first. The
depsgraph evaluation an apply's row asks for is the one the operator makes
itself. The first measure in a process imports NumPy (0.11 s cold in this
binary) when nothing imported it before. Each reading stops measuring after
0.1 s of its thread's CPU, so the worst case is about 0.1 s plus one row's two
largest measures (2 x 18.5 ms) per reading.

**Bytes.** Typical: 539 B for the four rows above (about 135 B a row). Worst:
about 8.2 KB (8,242 to 8,250 B in five runs): eight rows with 128-character names (``label``'s limit), live stacks
of five Booleans (four named), changed bounds and a fact, and
``booleans_more``. A script without a Boolean adds nothing.

**The kit's crowded cut.** Its new proof (the overlap less what the result
holds inside the cutter) is a second intersection: a castle-scale corner cut
through two touching wall parts went from 14.9 to 24.1 ms (median of nine);
uncrowded cuts and small crowded ones are unchanged (23.2 and 3.6 ms).

**Held out.** The measure was developed on boxes. A rotated, non-uniformly
scaled 40-segment cylinder cut by an icosphere reads 21.119 and 20.01 m³,
Blender's own ``bmesh`` volume of the same meshes to 1e-3; mirrored and
inward-wound objects, an open plane, a collection operand, a non-first
Boolean and selected objects behave as stated. No case uses a scene's names,
sizes or wording.

**Limits.** Closed parts that overlap count twice. ``object.convert``,
copies of evaluated meshes, Edit Mode and ``bmesh`` Booleans are not
observed. An object the view layer does not evaluate gets no world measure
and no fact. A Boolean that changed nothing but re-cut faces, whose rounding
exceeds 0.01% of a tiny operand's volume, shows its volumes without the
"unchanged" fact: a missed fact, never a false one. A live stack with any other enabled modifier, shape keys or a
deforming parent is left out. A target whose cutter moved is found only
when the cutter, or the target, is in the change list.

Tools aid, never handicap
=========================

The tool should only aid the model, never handicap it. A read-only study of
recorded runs (1,019 Opus transcripts, 3,017 session stores of all three
models, 23 Codex rollouts) costed every host mechanism that triggered a model
round. Three of them cost rounds, stills and verdicts while catching little
that a softer form would not catch too, and are softened or removed:

* **The self-check gate.** As a mandate it forced 124 correction rounds in 99
  Opus trials (1,214 s of model time). 118 of 170 failing checks were wrong
  checks on a right scene (91 then passed by copying the measured value into
  the target); correct parts were moved five times; two turns ended "failed"
  with no final still, one on a false alarm (wheels resting on a deck read as
  floating). Real catches came in 32 trials, of which at most 11 would
  otherwise have ended on a reply written before the checks ran. A failing
  check is therefore information (``agent_execution.rst``, "The script kit and
  self-checks"): the result round that protects such a reply stays (a failure
  the model has not seen, and once more after a plan that carried a reply);
  there is no mandate, correction cap, failed ending or job hold; the person's
  notice lists the checks still open however the turn ends, and a job's
  approval lists the checks failing when it starts.
* **Taken output names.** Refusing a re-render of a turn's own still, filed as
  an unknown failure, let the recovery barrier hold the request and end the
  turn without the corrected still (three recorded cases). A job never moves
  or replaces a file, and a taken name is no refusal: the job writes under the
  next free name, which its approval and start result state and its result
  names (``long_jobs.rst``, "Taken names").
* **The script routing screen.** It returned a valid script once, unrun,
  naming the typed kinds that could express it, and on Claude it rejected the
  whole plan and spent one of the two rejections. It fired in none of the
  recorded runs, so it is removed, with its hint counter; a valid script goes
  to its approval as sent.

**Replay of the recorded cases** (host only, unbilled). Each recorded gate
turn was replayed through the real ``ClaudeProvider`` loop with the model's
recorded plans in their order and each plan's recorded results (a job or
``LAST_JOB`` look the earlier host deferred answered as started). It shows
what the host does with the same plans, not what the model would have written
after the informational round, which only a live run shows.

* 99 turns (99 trials, 124 recorded gate rounds): 116 informational result
  rounds (a failure the model has already seen asks for at most one more,
  after a reply), none with a mandate; 9 deferred starts or looks and 1
  refused re-render now start; 94 turns complete exactly where they did, 1
  stops where the recording lacks results, and in 4 the host asks for one more
  round after the last recorded plan because that plan's own check failed (the
  recording has no answer to it). The two turns the gate ended "failed" are
  among those 4: given a reply, each completes, and the person's notice lists
  its open check (the floating-wheels false alarm in one, a real
  unsupported-wall catch in the other).
* The three refused re-renders: each recorded second start is admitted, its
  approval says "castle-final.png already exists and is never overwritten;
  this job writes castle-final-j3.png instead" (``-j2`` and
  ``tree-final-j3.png`` for the others), its start result's ``output`` is that
  path, the new still is published exactly there, the recorded render stays
  byte-identical at its name, and the jobs block names the new path.
* The script screen: no recorded run, transcript or session store holds its
  text.

The job tool's description, the job guidance and the Codex reference's job
line say a job never overwrites, a taken name gets the next free one, and the
result names it (description +3 B; job guidance +21 B, read with the job
family; Codex reference 14,471 to 14,490 B and Codex plan instructions 24,358
to 24,377 B); Claude's own instructions are unchanged. Not measured: model
rounds, seconds and verdicts with the informational round, which need a live
A/B.

Heavy-scene payloads
====================

On the 1,969-object heavy-castle scene (``scenario-heavy``: five everyday
edits) a warm Opus 5.5 trial took 12 rounds. The rounds' prompts carried host
bookkeeping a model never needs, and Opus checked three script edits with a
``verify=true`` round each (3-5 s), because the host had no proof of a
script's result. These changes alter only what a model reads, never a schema
or an instruction:

* ``model_results``: a model's copy of a result leaves out timing, a scene
  query's ``cache`` and ``budgets``, and a completed script's consent,
  confinement and per-flag Undo bookkeeping (its ``undo`` sentence stays),
  plus ``verified`` and ``after.scene_settings`` when the same message carries
  a turn-context observation that states them (never for Codex, a batch or the
  legacy observation). On the warm transcript: 1,465 B of two lookups and
  3,227 B of five scripts. A completed script's ``changes`` keep at most 24
  rows per list (``added``, ``removed``, ``modified``; ``MAX_CHANGED``, as its
  ``after`` keeps 24 objects), with ``omitted`` counting the rest: in a
  recorded run a rebuild script listed about 400 objects there, a 12.2k-token
  result that Codex cut.
* The continuation line: 447 -> 280 B (``plan_recovery.CONTINUED``).
* ``turn_context.MAX_FILL_OBJECTS``: a scene with more than 32 objects gets at
  most 8 scene-order rows besides the active, named, selected and changed
  ones; 30 such rows were 2,598 B of the first observation, 8 keep 1,909 B out.

Offline replay (no model call; each recorded user message re-encoded without
the bytes this change drops, through ``model_results`` itself; 0.49 tokens per
byte; the first request carrying a message writes it to the prompt cache,
every later one reads it): the cold heavy trial drops 1,289 B of results,
334 B of continuation lines and 1,909 B of fill, about 3% of its token cost;
the warm one 4,692 B, 1,002 B and 1,909 B, about 8%. Recorded small-scene
transcripts (certified programs, no continuation round) lose nothing and gain
nothing.

Program results. A program that added a 62-object batch returned 13,319
tokens and Codex cut the result; 36 of 138 recorded program results (GPT 6.1
Sol and Astra) were over 16 KB. Each object was listed three times
(``live_revisions``, ``changes``, ``delta.geometry`` with full-precision
bounds, a 16-number matrix and mesh counts), each passed check repeated its
verdict four times, and each operation read-back repeated its object's counts.
``program_model_result.compact`` gives a model one row per object: its live
row (handle, revision, role, alias) with world bounds to 1 mm and counts;
every check and failure stays, and ``angelo_scene_object`` returns the full
transform and topology by handle. Replayed over those 138 recorded results,
asserting that every handle, revision, check, object name and bound survives:
1,738,199 B -> 1,003,093 B; the largest 56,958 -> 23,404 B; over 16 KB 36 ->
15, over 40 KB 11 -> 0; median 6,740 -> 6,470 B (small results change little).
Recorded geometry rows predate the handle that lets an input's row join its
live row, so these merge only created objects: an upper bound.
Failed results are not compacted: their ``exact_schemas`` (241,789 B of the
remainder) are what a correction reads.

Request capture cost and stored history
=======================================

Every request captures a snapshot (``ui.capture_checkpoint``) on the main
thread before the model is called, so its cost is added to every message. The
capture path therefore does no whole-history work (``checkpoint_policy.rst``,
"Per-request cost and stored history"). When allocation scanned every stored
record and the worker's maintenance held the profile transaction for three
whole-history scans, a request's capture grew to about 0.5–1.2 s (median of
the last 100 of 600 requests, 700 stored snapshots): O(n) per request, O(n²)
per session.

* The timeline clock is trusted when it is exactly the file this process last
  validated or wrote (identity and content). Allocation then reads and writes
  only the clock; any other clock is checked against the whole history.
* The profile scan walks session and snapshot folders with held descriptors
  (``_private_child``) and reads each record directly: about 2.3 ``open`` calls
  per snapshot instead of 33 from re-anchoring every ancestor folder.
* Worker retention and orphan cleanup first probe without the transaction and
  take it only when something must be removed or re-checked.

Host measurement, with the product ``CheckpointStore`` and snapshot worker on
host Python at a 12-component profile path: ``reserve`` takes 1.6 ms and 55
``open`` calls with 0 to 700 stored snapshots; maintenance at 700 snapshots
holds the transaction 2.2 ms (the compaction check) and runs about 0.17 s in
the background. ``test_native_snapshot_history_scaling.py`` pins the contract:
a capture with 10 and with 1,000 stored snapshots makes identical system calls
and reads no record (the earlier code made about 28,800 ``open`` and 29,800
``stat`` calls at 1,000).

App measurement: the stress replay (``test_blender_program_stress.py``: 600
recorded requests back to back in one app process, about 0.27 s apart, a new
chat every six; the saved scene about 0.5 MB, the profile ending with 700
snapshot records) on a full-feature Release build, three runs, receipt phases
recorded per task: median ``checkpoint_ms`` 4.2–5.1 ms over the first 100
requests and 6.4–6.9 ms over the last 100 (p95 8.6–10.4 ms); each replay took
189–190 s, and all 3,600 tasks passed their host checks.

The remaining 1.6–2.2 ms rise over 700 snapshots is not the capture path:
capturing the replay's final document costs 1.3% more instructions than a
fresh one, and with worker maintenance switched off the rise is 4.1 → 4.9 ms.
The rest is the worker's lock-free whole-history scans (O(n), about 0.15 s at
700 snapshots) overlapping captures that arrive every 0.27 s and sharing the
GIL and CPU with them. At a human pace the scans rarely overlap a capture. A
fresh short session (one six-task chat in a new profile) does not pay for these
mechanisms: its runner wall time and summed task time match the whole-history
code within run-to-run spread.

The snapshot benchmark's history phase (``--history 10,1000``, default)
captures a one-cube document with the product ``ui.capture_checkpoint`` into
profiles already holding 10 and 1,000 snapshots (no snapshot worker), and the
ratchet keeps both repeat captures from growing as
``snapshot/history_<n>_repeat_capture_three_write_sum`` and ``_bytes``. Both cost the same instructions; current figures are in
`Private capture anchoring`_. Only the first capture of a process validates the
history (260–361 ms at 1,000 snapshots).

The snapshot worker stays O(n) per capture in the background, but cheaply: one
lock-free probe that calls ``lstat`` for each record and re-reads only changed ones,
store accounting only when an upper bound does not settle the byte limit, and
garbage collection only after a dropped manifest; probes and accounting pause to
hand the GIL back to the main thread about every half millisecond (``checkpoint_policy.rst``,
"The worker's per-capture probes"; measured in `Per-edit cost over a long
session`_). A new per-capture step must not list or read other records. Not
measured: Windows (its path backend validates each folder's whole chain),
profiles shared by several app instances (each foreign clock costs one
whole-history check), and histories beyond 1,000 snapshots in the app.

Private capture anchoring
=========================

The native request capture (``checkpoint_capture.CaptureOwner``) writes the
private checkpoint through held descriptors. Beyond Blender's own write, its
instructions are file-system protocol work in Python, and on macOS anchoring
dominated that work: a directory ``open`` for reading costs about 130,000
instructions and a ``stat`` about 20,000, so walking eight ancestor levels from
``/`` costs about 1.2 M, and a capture anchored 39 times (29 lexical identity
checks and 10 profile transactions).

* On macOS ``_private_directory`` opens an existing folder with one
  ``O_NOFOLLOW_ANY`` open, which refuses a link in any component as the walk
  does (the walk still creates missing folders and runs on other platforms).
* Callers that need only the folder's identity or ``*at`` lookups (the lexical
  identity check and the transaction root) open it search-only (``O_SEARCH``,
  about 30,000 instructions).
* The capture owner reuses its validation when a fresh read returns exactly the
  record bytes it last validated.

Every read, check and transaction remains. History profile, one-cube document,
minimum of nine repeat captures: 93.2 M instructions with per-walk anchoring;
49.9 M with the one-open anchoring; 47.9 M with the validation reuse; 44.2 M
with the search-only identity check; 43.0 M with the search-only transaction
root. Repeat-capture CPU time for the history profile is about 12 ms (median).

.. csv-table:: ``test_blender_perf_ratchet``, the retired ``snapshot/*_repeat_capture_net`` (min-minus-min net instructions, range of two runs)
   :header: "Operation", "Instructions"

   "``history_10``", "42.9–43.0 M"
   "``history_1000``", "42.8–43.5 M"
   "``dense_50mb``", "44.2–45.2 M"
   "``dense_200mb``", "48.9–49.0 M"
   "``dense_500mb``", "58.3 M (one run)"

``default/whole_request_snapshot`` measures 21.0 M.

What remains is the capture protocol itself, measured per call: five durable
metadata writes (about 2.2 M each: an exclusive create of 0.65 M and a rename
over the record of 1.2 M), the no-overwrite publication by hard link (4.5 M,
plus 0.5 M to remove the temporary name), the capture pin (1.3 M), ten
transactions (about 0.35 M each, mostly the lock file's open) and ten fresh
record reads (about 0.25 M each). An exclusive rename instead of the link would
save about 4 M; removing or merging protocol steps would change its
guarantees. The crash-safe capture behind full Sculpt recovery costs about
5 ms more CPU per request than the earlier capture (12 against 7 ms); the
ceilings of the four small and dense repeat-capture operations
(``perf_ceilings.json``) record this protocol. The checkpoint
seal adds no request-path instructions.

Live read tools
===============

``angelo_read_data`` and ``angelo_blend_file`` (``scene_intelligence.rst``,
"Live data paths" and "The file as a file"; toolset 36) are read-only tools
that need no approval. This section records what they are expected to return
and what they cost, as the selection rule asks (``native_coverage.rst``, "How to
continue"). The return is read from recorded receipts; the cost is measured in
the compiled app.

**What recorded work they replace.** In two recorded sessions that built one
scene almost entirely through reviewed Python (148 scripts in 52 turns, each
classified from its source), one script only read data (the modifier stacks of
twelve objects): with the read tools those facts are twelve paths in one plan,
without an approval. Turns there also ended on a failed lookup of an add-on's
settings type (``CyclesRenderSettings``) and on guessed property or socket
names. Since toolset 35 the lookup answers for add-on types and a failed lookup
or script error returns to the model in the same turn; the read tools add
current values beside the names (``scene.cycles`` lists its 110 settings, 64 a
call). Sockets of nodes a script has just created are not addressed by a reader
of existing data: the sockets of a node type are in no data path. A model
round in these sessions took a median of 43–52 s, which is why the reader is
stated exactly in a Claude plan instead of costing a lookup round before its
first use.

**Cost.** ``benchmark_blender_read_tools.py`` on the full-feature macOS arm64
Release build: medians of seven calls after one warmup, in process CPU time,
and the characters of the answer's compact JSON. The ``file`` cohort is a copy
of one of those sessions' final stage (168 datablocks, 1,483 nodes, 5.1 MB);
``synthetic`` is 20,000 objects on 2,000 meshes with 500 materials and 200
images whose files are missing (22,724 datablocks).

.. csv-table:: Main-thread cost and answer size
   :header: "Call", "``file`` CPU ms", "Characters", "``synthetic`` CPU ms", "Characters"

   "``scene.cycles``, 64 of 110 settings", "0.42", "5,231", "0.43", "5,232"
   "``scene.render``, 64 rows", "0.41", "4,025", "0.32", "4,025"
   "One property with its choices", "0.15", "274", "0.04", "258"
   "An unknown property (the valid names)", "0.25", "2,218", "0.31", "2,218"
   "``scene.objects``, 64 rows with handles", "1.54", "6,875", "7.93", "7,944"
   "An unknown object name", "0.10", "682", "11.86", "731"
   "A modifier stack with its values (``depth`` 2)", "0.52", "5,759", "", ""
   "64 nodes of the largest node group", "1.57", "5,204", "", ""
   "The same with each node's values (``depth`` 2, ends at the character budget)", "2.01", "10,983", "", ""
   "One node's input sockets", "0.22", "1,615", "", ""
   "File summary", "0.94", "961", "52.01", "4,044"
   "External files, one page", "0.26", "240", "38.57", "6,132"
   "Another .blend's datablocks", "18.01", "1,792", "78.25", "1,558"

The first call of a process adds about 70 ms of imports. The largest answer in
the suite's 1,584 calls was 12,340 characters. A read costs less than one frame
except the file summary on a document of 20,000 objects (52 ms: one pass over
22,724 datablocks and 200 existence checks) and a listing of another large file
(78 ms with Blender's library reader; the workspace-file path now uses the
native scanner below); both are single calls the person asked for, not
per-request work. The two rows that grow with the scene are the bounded pass
that marks which objects are in the bound scene (20,000 at most) and the name
list an unknown name answers with (4,000 at most).

**No cost on the common path.** The tools run only when called. What every
request carries is their bytes (``agent_integration.rst``, "Toolset 36": 585 B
of plan schema and 268 B of instructions for Claude, 1,024 B of declaration
and 287 B of instructions for a Codex plan thread), which the provider caches
with the rest of the prefix. No ratchet operation calls them.

Not measured: Windows and Linux; a file on a network volume, where each of the
file summary's existence checks is one ``stat`` that the tool cannot interrupt;
and model runs, so no end-to-end saving is claimed.

Workspace-file metadata scan
----------------------------

The workspace-file path uses a descriptor-only native scanner on a cancellable
worker (``scene_intelligence.rst``, "The file as a file"). Its limits: a
five-second deadline, 6 GiB of aggregate decoded and input work, bounded DNA
and codecs, and at most 4,096 retained strings. The isolated
``test_blender_metadata_scan`` fixture compares exact names, counts and order
with Blender's library listing for plain, gzip, Blender Zstandard and
Zstandard without its seek table. Measured on the macOS arm64 Release build
with a two-second deadline:

* A 199,361,797-byte file with 100,013 similarly named mesh IDs: 150 ms for its
  summary, 185 ms for the first 64 names, 258 ms for seven names at offset
  100,000 and 263 ms for the last two names, each matching native ordering.
* A 9,434,849-byte gzip file holding 2 GiB of unreferenced DATA padding: 986 ms
  for 4,295,281,596 decoded bytes across two passes. A tighter work cap
  returned ``LIMIT_EXCEEDED`` with no partial metadata; in-flight cancellation
  returned 0.230 ms after the stop signal.

The deadline is five seconds because the same 2 GiB fixture exceeded two
seconds on a Windows test machine, where the unlimited scan took 3.46 s. These
are single local samples, not a cold-disk benchmark; they establish bounded
storage, valid-format admission and exact deep pagination on these inputs, not
all-file or cross-platform parity.

Computed-property rows add 52 UTF-8 bytes for stored provenance or 53 for a
declared default in compact JSON (``value_source`` and
``computed_getter: not_run``); ordinary rows add none. The caller instruction
directing computed access to approved scripts adds 64 bytes once, not per row.

Rendered looks
==============

Method: ``angelo_view`` called through ``BlenderExecutor`` in a
factory-startup GUI of the macOS arm64 Release build (Apple M4 Pro), one
process per scene, a pending look awaited through the event loop as
``Controller._pump_events`` awaits it. Each sample is one call for the scene
camera at 768 × 432 with the image cache cleared and a new request; the first
call of a process is included. Scenes: the factory scene with EEVEE and with
Cycles, and three stage files of one island scene (Cycles on the Metal GPU,
kernels already compiled): A, 18 objects with 128 scene samples; B, 36 objects
with volumes; C, 55 objects with volumes, 16 subdivision and 14 node modifiers
and 4,096 scene samples. A look uses at most 32 samples. Other work shared the
machine and its GPU, so the figures are upper estimates with a wide spread, and
the first two tables were taken under different load and are not comparable
with each other.

.. csv-table:: Seconds per call at 768 × 432, median (range) of 3–5 calls
   :header: "Scene", "SOLID", "MATERIAL", "LIT", "RENDERED"

   "Factory scene, EEVEE", "0.01", "0.08", "0.09", "0.27 (0.27–0.61)"
   "Factory scene, Cycles", "0.01", "0.07", "0.08", "1.36 (1.35–1.86)"
   "Stage A", "0.02", "0.10", "0.09", "10.4 (8.4–15.4); on the CPU 16.9 (15.3–19.1)"
   "Stage B", "1.14 (0.45–1.72)", "5.36 (4.53–7.44)", "4.38 (2.06–4.55)", "44.4 (26.6–76.4)"
   "Stage C", "3.38 (3.19–3.41)", "2.37 (2.33–2.39)", "3.79 (3.76–3.84)", "113 (95–148)"

So a view takes 0.01–0.1 s on a light scene. SOLID, MATERIAL and LIT draw on
the main thread and block the interface for their seconds; a RENDERED look is a
background job and blocks nothing. LIT costs what MATERIAL costs. A look's time
is the scene's own render preparation, not its pixels or samples: stage C
rendered once at 768 × 432 and 32 samples took 130 s, of which 57 s passed
before the engine reported anything (Blender evaluating render-level
subdivision and node modifiers), 38 s were adaptive-subdivision tessellation
and 25 s sampling. Setting the render subdivision to the viewport's levels
changed that to 118 s, so a lighter preparation would have to come from the
scene, not from a setting of the look. On an idle machine a look of a tiny
scene (256 × 144, 8 samples) takes 0.31 s with EEVEE and with Cycles on the
CPU (medians of five, ``test_blender_rendered_view``), which is one poll
interval of the job manager.

Since 1.0.5 a running look is finished the moment Blender's render job ends
(``look_notify``): a 20 ms timer makes one native call
(``_angelo_jobs.progress``) while the look renders and runs the job manager's
tick at once when the render is gone, instead of waiting up to 0.25 s for the
manager's own poll, which stays as the fallback and for every other job.
``ANGELO_LOOK_NOTIFY=0`` turns it off. The manager's poll starts with the
look, so the time saved is what remained of the poll interval when the render
ended: between 0 and 0.25 s per look, about 0.125 s on average over look
lengths. Measured in the pre-registered render-preparation campaign
(2026-10-23, Mac M4 Pro, Cycles on the Metal GPU, the factory scene, 768 ×
432, every look a new request), on the perf/render-prep-reuse build with only
this watch switched on against the same build with every option off: median
0.618 s per look (p75 0.627, range 0.596–0.644, 70 looks over 10 runs) against
0.777 s (p75 0.785, range 0.767–0.794), so 0.16 s per look on that scene.
Kept render preparation, measured in the same campaign, does not ship: on heavy scenes the half-of-memory budget refused most looks and
the kept data raised the next render's peak memory by up to half (stage C: 24 to 37 GB).

.. csv-table:: RENDERED, whole frame and a region, seconds per call (median, range)
   :header: "Scene", "Whole frame, 768 × 432", "Region 0.3 × 0.3 of the frame, 576 × 324 of a 1,920 × 1,080 frame"

   "Factory scene, EEVEE", "2.51 (1.30–3.31)", "3.64 (3.26–4.61)"
   "Factory scene, Cycles", "3.95 (3.04–4.46)", "3.34 (2.73–4.21)"
   "Stage A", "21.4 (17.4–32.1)", "17.6 (13.9–21.5)"
   "Stage B", "57.0 (55.2–63.1)", "49.9 (47.3–56.8)"
   "Stage C", "173 (167–198)", "151 (124–185)"

A region costs what the whole frame costs, because preparation dominates; it
buys detail, not time. Stage C's whole-frame looks outlasted the 120 s budget
while preparing and were stopped 20 s into their samples with all 32 rendered.
Making the delivered picture opaque (decode, checkerboard under transparent
pixels, encode) takes 29 ms at 768 × 432 for a render (4 ms decoding, 19 ms
encoding) and 125 ms for pure noise, the encoder's worst case.

.. csv-table:: A finished picture shown with ``image``, seconds per call (median of five)
   :header: "Source, 3,840 × 2,160", "To 1,024 × 576", "Region of 960 × 540 source pixels"

   "8-bit PNG (9.6 MB)", "0.16 (to 512 × 288: 0.15)", "0.15"
   "OpenEXR, float (133 MB)", "0.39", "0.37"
   "Blender's Render Result", "0.34", "0.30"

These calls run on the main thread. On an idle machine the PNG took 35 ms to
512 × 288 and 24 ms for a region (``test_blender_rendered_view_pixels``).

**Image tokens** (computed, not measured), from the provider's published rule
of about one token per 28 × 28 pixel patch for Claude: 256 × 144 about 50,
512 × 288 about 190, 768 × 432 about 420, 1,024 × 576 about 750, a 512 × 512
view about 335 and a 1,024 × 1,024 view about 1,340. The same sizes by
32-pixel patches, an estimate for the Codex models: 40, 144, 336, 576, 256 and
1,024.

**What a kept view costs.** A Claude request keeps up to twelve views
(``claude_evidence.VIEW_IMAGE_LIMIT``; ``agent_execution.rst``, "Visual
observation"), each captured again after an edit and attached to every later
round: twelve views are about 4,000 tokens a round at 512 × 512 and 16,000 at
1,024 × 1,024 by the rule above. Their capture draws on the main thread, so
one edit spends at most five seconds on it (``claude_evidence.REFRESH_SECONDS``),
the views asked for most recently first; the rest are reported as stale and
captured when the model asks again. Measured with twelve kept axis views (six
in SOLID at 512 pixels, six in LIT or SOLID at 768), every capture uncached as
after an edit:

.. csv-table:: Seconds one edit spends capturing twelve kept views again
   :header: "Scene", "Without the bound", "With the five-second bound"

   "Factory scene, stage A", "0.1–0.4", "0.1–0.4, all twelve captured"
   "Stage B", "1.5 (SOLID); 6.7 (SOLID and LIT)", "1.4; 4.4, all twelve captured"
   "Stage C", "20.4 (SOLID and LIT); 26.4 (SOLID)", "3.8; 4.2, two captured and ten reported as stale"

One view of stage C cost 0.8–3.5 s in these runs. The bound is five seconds
because that keeps every kept view of the light and medium scenes current while
the heaviest stage loses nine tenths of its refresh time; the view asked for
most recently is captured whatever it costs, so a model always gets the one it
last looked at.

**Why a look rather than a screenshot of a Rendered viewport.** A screenshot
of a 3D viewport in camera view and Rendered shading
(``screen.screenshot_area`` of a 1,488 × 1,380 viewport, 57–480 ms each)
renders viewport-level data and shows something within seconds: stage C was
recognisable at 10 s and fairly clean at 120 s. A look renders render-level
data, what a final frame has, so on a heavy Cycles scene its first picture
comes later (stage C's preparation alone took 60–150 s). In exchange a look is
bounded, says when it is partial, changes nothing the person sees, and returns
the camera's frame only: in the screenshots the frame was 527 × 420 pixels of a
1,488 × 1,380 picture (about 2,600 tokens by the rule above, against 420 for a
look) with the camera border, overlays and whatever lay over the area.

Limits: three to five calls per cell; one island scene; image tokens are
computed, not measured. Windows, Linux, other GPUs and a first GPU render after
an install (kernel compilation) were not measured.

Scene check after views
=======================

After every RENDERED look and motion strip, ``view_scene_check`` compares the
document with its state before (``agent_execution.rst``, "Checked after every
view"). ``benchmark_blender_view_scene_check.py`` measures what that adds: its
reading before the view plus its reading and comparison after, on an
unchanged document, in a background factory-startup process of the macOS arm64
Release build, one warmup and seven samples per case. Each sample follows a
garbage collection with the collector then off, and counts the main thread's
instructions (a collection inside one sample had doubled a 3.6-million count).
``no_handlers`` is the check when no Python from add-ons can run during the
view (every view in the Read-only and Ask modes, and in the Auto modes without
such handlers); ``handlers`` the check when add-on depsgraph-update handlers or
message-bus subscribers can (Auto modes). Scenes: *small*, the factory scene;
*medium*, 50 objects with their own grids (204,800 vertices) and two
1,024 × 1,024 images; *large*, 2,000 objects with their own grids (1,058,000
vertices), one shared node material, a Bevel on every tenth object and the same
two images. Instructions are the range of four run medians; CPU is the median
and range of the run medians.

.. csv-table:: One check (readings before and after a view)
   :header: "Scene, case", "Instructions", "CPU"

   "small, no_handlers", "3.13–3.19 M", "0.83 ms (0.71–1.02)"
   "small, handlers", "18.9–19.1 M", "4.4 ms (4.2–4.5)"
   "medium, no_handlers", "3.64–3.74 M", "1.05 ms (1.02–1.11)"
   "medium, handlers", "39.8–40.0 M", "8.1 ms (8.0–8.4)"
   "large, no_handlers", "26.3–26.6 M", "7.0 ms (6.4–7.5)"
   "large, handlers", "852–855 M", "161 ms (159–166)"

What the check reads, and why the scope without add-on Python is enough:

* **Without add-on Python** the check reads data-blocks by session UID (added
  or removed), the bound scene's settings and custom properties, and every
  object's transform and render visibility, numbers a whole collection at a
  time (``foreach_get``, one call per property, compared byte for byte), but
  not each object's data, parent, materials, modifiers and mesh positions.
  Nothing the view itself runs changes those: a render and a frame change
  evaluate copies of the data, and the view's own writes are put back and
  checked by its restore. What runs beside it (the person while a look renders,
  an add-on's timers, which Python cannot list) adds, deletes and moves things,
  which is still reported. A strip's frame changes can put an unkeyed edit of an
  animated setting back to its keyed value, as scrubbing does; on an object's
  transform that is reported.
* **Mesh positions** (with add-on Python) are read from the position
  attribute's own array, 2.3 million instructions per million vertices against
  416 million for ``MeshVertices.foreach_get("co")``, which reads vertex by
  vertex, and summed with CRC-32 (5.1 million per million vertices; BLAKE2b
  155 million). A CRC-32 misses an accidental change with a probability of
  2\ :sup:`-32`. ``MAX_VERTICES`` is ten million, so the large scene is compared
  whole; a shared mesh is summed once.
* **Image pixels** are never read: arrays without a fixed length
  (``Image.pixels`` is the one writable data-block array of that kind) are
  skipped. A reading that included them took 98 G instructions and 10.1 s of
  CPU per check on the medium scene, nearly all of it the two 1,024 × 1,024
  images' pixels.
* Each struct's list of properties to read is built once per session.

The ratchet keeps the six cases from growing (``view_check/<scene>/<case>``,
``test_blender_perf_ratchet``); their ceilings are the highest of the four run
medians (their ranges stayed under 2.5%, so the tolerance is the default 5%).

**Every other view that writes** (``view_policy``) first checks what add-on
Python its writes would run. Without any, the same benchmark measures, per
view and nearly independent of the scene (four runs): a SOLID capture 0.12 M
instructions (0.09 ms of CPU: the message bus for its display settings); a
MATERIAL capture over a SOLID viewport 0.17–0.22 M, and 2.3 M (0.8 ms) with
2,000 objects, because that type change also reads every object's mode,
natively, for sculpt sessions; a saved Render Result or pass picture 1.96 M
(0.45 ms), most of it listing the image-format settings the save may write. The
ratchet keeps the nine ``view_policy/<scene>/<case>`` cases from growing
(tolerance twice the observed range where that exceeds 5%: up to 20% for the
0.12 M display case).

Names Blender gave a script's data-blocks
=========================================

Every reviewed script reads the document's data-block names and session UIDs
before it runs and, when it completes, the same collections again
(``script_renames``; ``agent_execution.rst``, "Names Blender gave in place of
the names asked for"), and while it runs observes name assignments and
file-reading calls (``script_rename_watch``). Measured by
``test_blender_script_renames.py`` (``costs``) in a background factory-startup
process of a macOS arm64 release build: this process's CPU time with the
collector off in each sample, best and median of nine; a script path is a
script through ``BlenderExecutor.execute``, seven runs each way, alternating,
after two warm-up runs (the first restore point holds the whole file). Scenes:
the factory scene; 2,000 objects with their own cube meshes and 300 materials;
20,000 such objects and 3,000 materials, also with one object linked from a
library. Load average 4.5 to 5.5. Runs at loads of 5 to 18 measured up
to 10.0 and 16.0 ms for the readings and +14 to +24 ms on the empty path at
20,000 objects.

.. csv-table:: Names before and after a script (CPU, best and median)
   :header: "Scene", "Data-blocks", "Before", "After, one object added", "Empty script path without / with"

   "factory", 14, "0.07 ms (0.07)", "0.10 ms (0.12)", "2.75 / 2.88 ms (2.94 / 3.01)"
   "2,000 objects", "4,314", "0.52 ms (0.74)", "1.29 ms (1.37)", "23.1 / 24.0 ms (23.3 / 24.4)"
   "20,000 objects", "43,014", "8.2 ms (8.8)", "13.8 ms (14.3)", "105 / 117 ms (106 / 119)"
   "20,000, a library linked", "43,017", "15.8 ms (16.1)", "15.6 ms (16.2)", "not measured"

So about 0.2 µs per data-block before a script and 0.3 µs after it: 0.1 ms on
the factory scene, 1 ms (4% of an empty script's path) at 2,000 objects and
12.5 ms (12%) at 20,000, less of any script that does work. The cost is the
reading: per collection one ``keys()`` and one ``foreach_get`` of
``session_uid`` (``view_scene_check._inventory``). Counting a data-block list
walks it, as ``foreach_get`` does again, so the inventory takes the count from
the names it read; measured apart the same way on 20,000 objects and meshes
without geometry, a reading took 8.4 instead of 10.6 ms, a gain the scene
check after views shares. The comparison has no Python step per data-block (a
dictionary by UID, ``map`` and ``compress``): 5.4 ms, measured apart, for two
changed 20,000-entry collections, nothing for unchanged ones. A file that
links a library also reads each data-block's ``library``, and a local one's
override, one by one: the reading before a script nearly doubles (15.8 against
8.2 ms at 43,017 data-blocks). Every kind models look up by name is read
however many there are; object data is read while the whole stays within
``MAX_NAMES`` (50,000), each kind counted first (one walk of its list) and
skipped before its names are read when it would pass the limit, so objects
past the limit on their own (52,000 beside 20,000 meshes) are still read, and the meshes are named in ``not_checked``; the suite
checks it with the limit below the factory scene's three objects.

While the script runs, every attribute it sets on a data-block passes through
one Python call (``bpy.types.ID.__setattr__``): 68 ns more per set, 263
against 332 ns for ``material.pass_index`` set 100,000 times (best of five),
about 1.4 ms for 20,000 sets; setting ``hide_render`` on 20,000 objects took
5.2 to 5.9 s across runs, with or without it. A file-reading call reads the
covered kinds' session UIDs before and after it.

Bytes, from a replay of the recorded scripts: the 869 scripts that completed
in 550 recorded Angelo trials (Claude transcripts and Codex rollouts of the
one-shot, scenario and gate runs; a script in a plan the host rejected,
or one that raised and was rolled back, changed nothing and is left out), each
trial from Blender's factory scene through the real executor, scripts only (no
typed program in between). 793 completed again; 67 raised for want of what a
typed program or a heavy start file had made; 9 were skipped for writing
files, rendering or opening files. 44 results (5.5%) carried the field, 668
names in all: 658 meshes (primitives added again whose objects were renamed,
meshes made under the name of one left behind, and 30, in 24 castle scripts, a
mesh made by ``new_from_object`` and named while the mesh it replaced still
held the name, which only the observed assignment shows), 6 materials, 2
cameras, a light and a World. Field bytes (compact JSON): median 56, 90th
percentile 165, largest 424, or 4.9 B per completed script. Without the
observed assignments, the same replay lists 638 names in 20 results
(median 68 B, largest 424 B); on those results plain names without runs took a
median of 141 B and listed fewer names under the cap, and ``{"name",
"taken_by"}`` rows 387 B (largest 901). A worst case, twelve run entries of
two 255-byte names each and every kind counted or unread, is about 7 KB.
Static counts behind the field's order (1,279 distinct scripts): objects were
looked up by name in 764, materials in 235, Worlds in 93, lights in 43 (a Sun
reused) and collections in 41, meshes in 7 though 696 created one by name,
cameras in none though 552 created one; none imported, appended or overrode
anything.

Limits: the replay starts every trial from the factory scene and runs no typed
programs, so clashes with what a start file or a typed program made are not
counted; the timings are of one machine under load.

Native automatic reads
======================

Every automatic read goes through the native address ABI
(``scene_intelligence.rst``, "Non-mutating access"). Method:
``read_coverage_sweep.py`` on the planted read-tools document
(``ANGELO_READ_COVERAGE_MAX=20000``, both records complete) in background
factory-startup processes of the macOS arm64 Release build, against the same
build with the earlier Python reader, in four interleaved pairs. The sweep
records the instructions and cycles the process retired
(``proc_pid_rusage``) next to wall and CPU time. The answers are identical
apart from catalogued rows (``read_coverage_compare.py``: no regressions).

.. csv-table:: One coverage sweep (about 3,450 reads at depth 2), range over four pairs
   :header: "Reader", "Instructions", "Cycles", "CPU"

   "Python", "23.37–23.60 G", "8.12–10.63 G", "2.68–4.95 s"
   "Native", "20.03–20.27 G", "8.28–10.67 G", "2.80–4.57 s"

The native reader retires about 14% fewer instructions; cycles and CPU time are
at parity (within ±5% pair by pair). Repeated reads of a struct with rows cost
fewer cycles than with the Python reader (``data.objects[0]`` 3.9 M against
6.0 M, ``scene.render`` 3.1 M against 3.8 M); the sweep reads each path once,
so copying each type's declarations the first time is part of its cost.

**Per-read fixed cost.** A read that returns almost nothing costs about 0.1 M
cycles (about 0.03 ms) more than with the Python reader, measured warm:
``scene.frame_current`` 525 K instructions and 313 K cycles against 457 K and
215 K, an empty collection 923 K and 556 K against 604 K and 148 K (cycles of
these short reads vary between runs). Against model latency that is not a
measurable cost. It goes to opening a read, describing its root, the final
check and closing (106 K instructions); single ``reference`` calls (about 130 K
each, 2,352 in a sweep); building and encoding node records whose address
Python already knows (29,720 in a sweep); and allocating and freeing the native
transport value trees (128 bytes per value; about a third of a warm value
page's samples).

Visible progress while a plan is written
========================================

A typed plan is written whole before it runs, so a turn shows little until
then; the chat lists the plan's steps under the live row as they stream
(``agent_execution.rst``, "How long a turn may run"). Display only: no model
request, schema, instruction or output token changed, and what runs is
unchanged.

**Where the wait is.** On recorded arched-footbridge one-shots (Opus 5.5,
effort low, five trials) the plan call began about 7.5-11 s after sending
(6.6-10.2 s after Claude Code received the message): about 0.9 s of runtime
start, about 1 s to the first token and 5-9 s of thinking,
whose text Claude Code omits in print mode, so the thinking row shows only its
duration. The plan then took 7.5-12.5 s to write. Reading the plan cannot
shorten the first part. Setting Claude Code's ``showThinkingSummaries`` in the
chat's ``--settings`` (three trials, Claude Code 2.1.293) gave no summary text, and
GPT-6.1-Sol's first reasoning item often carries no summary either; Codex's
app-server (0.162) forwards no tool-call input as it is written, so the code of
an ``exec`` call shows only when it runs.

**Replay of every recorded plan.** ``PlanProgress`` streamed each of 676 recorded
Claude plans with operations in 16-character
fragments at its own recorded pace (its writing time from the end of its
thinking block, or 260 characters a second): every plan shows a step, the
first a median 0.49 s after the plan call began (0.74 s at the 90th
percentile). A detail of the plan's names alone would have named something
in 81 of them, at the same moment; the other 595 were short edits (a median 0.94 s of plan) that showed
only "Preparing the next steps".

**Live confirmation** (three trials each on the arched footbridge; the
live-eval session polls what the chat draws, ``oneshot_tests.rst``):

.. csv-table::
   :header: "Run", "Passed", "Plan began, s", "First planned row, s", "Reply, s", "Wall, s"

   "Recorded, without plan rows, Opus 5.5 (5 trials)", "5 of 5", "about 7.5-11 (from transcripts)", "–", "16.2-22.7", "17.9-23.9"
   "With plan rows, Opus 5.5", "3 of 3", "8.7-9.6", "9.3-10.2", "18.1-18.6", "19.9-20.0"
   "With plan rows, Opus 5.5 (thinking-summary setting)", "3 of 3", "7.6-13.1", "8.1-13.8", "14.6-26.7", "15.4-28.0"
   "Without plan rows, GPT-6.1-Sol", "3 of 3", "–", "–", "–", "45.0-57.5"
   "With plan rows, GPT-6.1-Sol", "3 of 3", "–", "–", "–", "45.0-61.6"

Opus's first row came 0.5-0.7 s after its plan began, as the replay predicts;
a detail of the names alone would have shown the same first post at the same
moment, which the recorded report did not count (it counted the reply). Sol built
the arch with ``exec`` calls (a lookup, a script, a program, a view) and ended
with an empty plan, so no planned row appears; its first visible activity is
its first tool row, 5.7-8.0 s after the message in its rollouts, and a
reasoning title (7.5-47.9 s). Output tokens were unchanged (Opus 1,753-1,977
against the recorded 1,723-2,013). The stand-in suite ``test_blender_plan_progress``
draws the first row 0.2-0.3 s after the stand-in sends its name.
