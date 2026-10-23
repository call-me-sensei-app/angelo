.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Fast-Tier Reliability
*********************

Auto routing sends work to a cheaper model and escalates only on a failure, so
every avoidable failure or extra model round on a fast tier costs money and
latency. This document lists the patterns that made fast tiers (Claude Sonnet
5.5, Haiku 5.5) need extra rounds or fail on Angelo's task sets, what the host
does about each, the harness options that keep a measurement clean, and the
tests that pin the behaviour. It is not a guarantee for other prompts.

Failure patterns
================

Extra model calls beyond the first of a task, by cause:

* **Lookups.** The observation delta did not list an object the message
  mentioned, a question needed dimensions the observation did not carry, or
  the model read an object before a recipe from a wrong belief about
  revisions.
* **Host rejections.** An expected check named the object being created, or
  the plan invented a ``$alias`` for an existing object.
* **Runtime schema rejections.** An object name where a handle belongs, or
  program steps without the ``{tool, arguments}`` shape.
* **Runtime nudges.** A plain-text answer instead of a ``StructuredOutput``
  call.
* **Model-chosen verify rounds** on an edit the host had already certified.

Failures and wrong results:

* **Outside input, then a misleading stale-binding error.** Keystrokes that
  reached a window started animation playback. A staged program is bound to
  the current frame, so staging found a different binding and the request
  ended with "The target changed after approval", although nothing had been
  staged and no object had changed.
* **Guessed modifier name after a context rebase.** The first turn after a
  rebase configured a modifier by a guessed name; the stage rejected it with
  "Choose an existing staged modifier", and a stage failure ended the request.
* **Lookup-only plan ended the turn.** The delta listed no objects, the model
  looked the object up with ``verify`` omitted, and the host ended the turn
  after the lookup, so the edit never ran.
* **Partial plan.** The model read the object first, believing a recipe
  needed a revision it lacked, then applied only part of the request.

No race exists between the request queue, checkpoint capture, snapshot
ingestion, a scene reset and the staging timer: see Validation.

Host behavior
=============

Frame changes during a request (``program_context.py``)
-------------------------------------------------------

A program stays bound to its approved context: document generation, scene,
view layer, current frame, every declared input's revision and fingerprint and,
when the program declares the scene, its settings revision
(``program_context.ProgramBinding``). The current frame is the one part an
artist routinely changes without touching an object. So:

* While animation playback runs, staging waits (status "Waiting for animation
  playback to stop"), bounded by the call's normal expiry.
* When only the frame moved, the same approved program is rebound once at the
  current frame if every declared input is frame-independent (no animation,
  drivers, constraints, parent, physics or time-reading modifier or node) and
  provably unchanged: its revision recomputed at the approved frame equals the
  declared one and its dependency fingerprint is identical; a declared scene's
  settings revision has no frame field and must simply be equal. That revision
  comes from one capture that must itself be exact
  (``ObjectFingerprinter.revision_at_frame``): a capture that ran out of its
  work budget or meets an untracked dependency is refused, never hashed, so
  the program is not rebound. The rebound
  program differs only in those revisions and is recorded as the call's staged
  program. A frame move found while staging or just before commit discards the
  stage and stages the same program again. Everything else (Undo, Redo, load, another
  scene or view layer, a changed or time-dependent input, a second move) fails
  closed, with an error naming what changed and what to do.
* A still object's revision carries no frame at all
  (``scene_fingerprints.context_field``, ``revision_time.still``: the rule
  above, with a parent, a Mirror or Array object and a focus object followed
  and required still rather than refused), so a frame change made before a
  program is written, by the person or by the model's own script, does not
  stale its binding either; a time-dependent object's revision still does.
  A frame move between approval and staging still rebinds only under the
  narrower rule, so a still child of a still parent is refused there
  (STALE_DEPENDENCY).
* Receipts count ``frame_rebinds``, ``staging_restarts`` and ``playback_waits``
  and a content-free class per failed tool result (``failure_<class>``:
  ``frame``, ``binding``, ``check``, ``modifier``, ``expired``, ``other``;
  ``failure_codes.py``), never the error text.

A preview being accepted is not rebound (its stage is consumed); a moved frame
fails its Accept with the frame named.

Fewer avoidable rounds
----------------------

* Observation deltas always list the objects the message names, even when
  unchanged, and whole (``context_ledger.TurnBlocks.observation``;
  ``agent_stack_integration.rst``). Each such entry replaces a lookup round.
* A plan of lookups only always gets the round that reads their results, as if
  ``verify`` were true. A standalone ``angelo_scene_verify`` still ends the turn.
* An expected check whose target equals the ``name`` of exactly one creating
  operation is bound to that creation's alias, assigning ``created_<n>`` when it
  had none (``program_spec._created_name_targets``). An undeclared existing
  object could not be checked anyway, so no other reading exists. A context
  with short identifiers leaves such a target to that binding instead of
  inferring an observed object for it (``program_spec.observed_check_references``),
  so inference never refuses the name first. A binding by name also adds an ``object_name`` check that the
  creation got that name: Blender suffixes a taken name, and not every creating
  kind derives a name check (``armature_create`` derives none), so the bound
  check could otherwise pass on ``Rig.001``.
* The host validates every step of a plan before the first runs, and
  ``material_assign`` never looks a material up in the file, so a program
  cannot assign a material that a script of the same plan makes (for example
  a script for a deck and its material, then a program that gives posts and
  rails that material); such a plan costs a correction round, and the fix is to
  state the material in the program and run the script after it. Claude's plan
  instructions say so (+162 B in the cached prefix), and the refusal names
  that plan (``material_rules.not_in_program``).
* A program that fails on its isolated stage before any live write returns to
  the model once for a corrected plan (approved again), like a plan the host
  rejected. Never for a result that may have changed the scene, needs cleanup,
  was declined, expired or stopped, or whose binding went stale. The
  budget is ``plan_recovery.FAILED_EDIT_CORRECTIONS`` (2), counted apart from rejected plans. The modifier error lists the
  object's modifiers of the declared type. The failed program may sit
  anywhere in the plan, and when earlier operations of the plan ran the
  model is told what ran instead of "nothing changed". A reviewed script that
  raised also returns to the model, under its own budget of 5, after the host
  stepped back over its changes with Undo and read the scene back
  (``agent_integration.rst``, "Failed plan steps").
* Reference errors name the offending target and the fix. A name where an
  object handle belongs gets an error that names the fix
  (``binding_refusals.not_a_handle``).
* The always-sent plan contract states answers only through
  ``StructuredOutput``, the ``{tool, arguments}`` operation shape and
  handle-only targets: +162 bytes, +76 tokens per call by
  ``token_accounting.py``'s tokenizer fit (+0.3% of the about 28k-token
  cached prefix).

Not changed: a fast model sometimes sets ``verify`` on an edit the host
certifies; ending such a turn could drop a dependent edit the model meant to
submit, so only the model can save that round. Observed dimensions answer
dimension questions without a lookup.

Harness
-------

``eval_live_agent.py`` and ``bench_mcp_vs_angelo.py``:

* ``--input-isolation on`` (default, both sides) starts trial windows with
  ``--enable-event-simulate``, so real keyboard and mouse input never reaches a
  trial. Blender's main loop then does not idle-sleep (one busy core); timers
  fire without the idle delay. ``off`` reproduces runs made without it.
  ``run_native_suites.py`` isolates every window suite the same way, the
  live-eval dry run included (``agent_execution.rst``).
* ``--window-state behind`` (default, both sides) passes ``--no-window-focus``,
  so trial windows open behind the person's applications and never take focus.
  On macOS stock Blender still activates and orders its window in front, and a
  covered Angelo window is drawn but not presented, so the sides' window states
  differ: the run plan records each side's state (a run summarized again, and
  reused MCP results, keep what their run recorded, or say it recorded none),
  and the run prints a ``WINDOW STATE`` line and lists the difference among the
  report's threats to validity. ``front``
  gives neither side the flag; both activate and draw in front as a person uses
  them, on a machine nobody is using. ``final_runs`` passes the state both sides
  share (``gui_launch.matched_window_state``: front on macOS, behind elsewhere)
  to every two-sided step (measurements in ``performance.rst``).
* Each task records ``environment`` (frame before and after, playback,
  ``external_input_suspected``).
* ``--after-failure reset`` restores the reference state a task needs (frame 1,
  the 2 m eval cube, a Bevel modifier for a "Change the bevel" prompt) through
  ``eval_live_tasks.reset_for_task``, so every task of a trial is graded; the
  record keeps ``harness_reset``. The default stays ``skip`` for comparability.
* Receipts are found by identity rather than by a count, so the store's
  256-receipt bound does not hide them.
* ``bench_mcp_report.angelo_transcript_steps`` counts model calls per task
  from the Claude Code transcript by matching prompts in task order, so a
  repeated prompt is credited to the right task.

When checking a run, watch the receipt counts ``frame_rebinds``,
``staging_restarts`` and ``failure_<class>`` and the records'
``environment.external_input_suspected``: a nonzero value means input reached
a trial despite isolation.

Validation
==========

* Host tests: ``test_native_program_context.py`` (frame recovery on the real
  controller with a frame-stamped fake executor), ``test_native_fast_tier.py``
  (each pattern above, with mocked rounds and native calls), and the harness
  tests.
* ``test_blender_program_stress.py`` (GUI, event simulation, no provider):
  recorded plans replayed through the unchanged live-eval driver, by default
  100 fresh chats of 6 tasks under six timing modes, including frame steps and
  playback. ``ANGELO_STRESS_ADDON_ROOT`` loads the add-on from another
  checkout, for example a commit before the frame-change handling, which
  reproduces "The target changed after approval" on frame-step tasks.
* ``replay_fast_tier_cases.py RUN_DIR [RUN_DIR ...]`` (offline) replays a
  recorded run's chat records and Claude Code transcripts through today's host
  code and reports, per pattern (``named_deltas``, ``lookup_only``,
  ``superseded``, ``stage_failures``, ``rejections``), what the host now does
  with the recorded data. It never invents a model's next plan.
