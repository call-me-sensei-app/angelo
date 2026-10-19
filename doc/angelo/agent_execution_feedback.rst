.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Agent execution: design constraints
***********************************

The principles the execution layer (``agent_execution.rst``) follows, and the
approaches it deliberately does not take, each with the reason.

Principles
==========

* Prioritize read-only scene intelligence, declared native programs and local
  expected-result checks. The old basic inspector is not sufficient for most
  modeling questions, and unrestricted Python requires individual approval.
* Use document-generation-scoped native IDs. Renames must not invalidate an
  identity; loading/Undo replacement must not reuse stale references. Do not
  create duplicate-prone UUID custom properties as a side effect of reading.
* Prefer BMesh/native data operations over UI-dependent mesh operators. Bind
  element indices to observed revisions and within-program topology generations;
  offer closed rule-based selectors rather than unbound integer indices.
* Use installed RNA/BMesh documentation as read-only API lookup. This is
  especially useful for current slotted animation APIs. It is not arbitrary
  evaluation, and RNA metadata cannot grant permission or establish safe work
  limits. Mutation schemas retain explicit allowlists and cross-field checks.
* Persist metadata-only timing receipts, including snapshot cost from before
  the first capture, approval waiting, model rounds, first native action,
  staging/commit/verification and completion. Separate wait time from execution.
* Add explicit checkpoint capture/retention controls, safe cleanup and clear
  missing-checkpoint UI. Do not introduce a custom on-disk geometry-delta engine.
* Use a host-owned project brief, with verified facts sourced from actual tool
  callbacks rather than model claims. Saving a note does not approve future
  edits, files, references, network access or billing.
* Grade reusable task fixtures against actual native postconditions, not just
  tool completion or speed. Comparing providers needs real model runs under
  matched conditions; mocked/local fixtures cannot establish a winning model.

What the host deliberately does not do
======================================

**Deferred snapshots are opt-in.** A checkpoint restores the state before each
request message. Capturing before the first approved edit produces a
different boundary, particularly if the artist edits while waiting for
approval. Every Request remains the default. Before First Edit is
an explicitly labelled human setting, with the real checkpoint attached only
when captured. No tool can silently change this policy. Retention defaults to
preserving history and never deletes live recovery dependencies without an
explicit reviewed prune action.

**Do not inject exceptions into Blender's main thread.** A watchdog cannot
safely interrupt arbitrary native kernels. A signal-raised exception may occur
at an unexpected instruction, and Python signals do not interrupt arbitrary
long C calculations. See the Python documentation on
`signal handler execution <https://docs.python.org/3/library/signal.html#execution-of-python-signal-handlers>`__.
Typed native programs use safe operation-boundary cancellation. Arbitrary
privileged Python retains an honest interruption limitation; long work should
be moved to declared operations or explicitly isolated processes, not made
apparently safe by asynchronous exception injection.

**Do not promise general rollback of unrestricted Python.** A reviewed script
can write files, call external services, alter Undo history or change global
application state. Blender Undo cannot make those effects transactional. The
new bounded programs instead stage isolated copies and validate before commit;
failures discard uncommitted data. General Python retains visible review,
partial-change reporting and explicit checkpoint recovery, not blind retries.
The one thing the host does after a script raised is step back over that
script's own Undo step and read the scene back (``agent_execution.rst``,
"Reviewed Python that raises"). That is Blender's Undo over the scene,
reported only when the read-back matches the scene from before the script. It
promises nothing about files, services, application state or a script that
altered the Undo history itself, and such a script is left alone.

**Undo-only preview is insufficient across UI yields.** An artist or another
chat may edit after a preview. Undoing to discard it could revert their work.
Staged copies with exact commit preconditions avoid that conflict. Temporary
data must not leak into user saves or Undo snapshots; completed previews and
yielded stages require explicit isolation and owned cleanup.

**Do not trust depsgraph events as complete sculpt mutation versions.** They
are useful for scene cache invalidation, but live PBVH/CCG sculpt changes and
Undo can bypass ordinary geometry reevaluation. Full exact mutation checks
remain the fallback. Region-level sculpt caching is deferred until native
write coverage and profiling justify it, not approximated with pointer/count
checks or optimistic invalidation.

**Do not silently change the artist's active editing mode.** BMesh kernels
operate on private copies and their internal operator overrides restore the
host context. General programs currently require Object Mode inputs; unsaved
Edit Mode data and live Sculpt/PBVH state are not equivalent to the stored base
mesh. The specialized native sculpt tools remain the route for live sculpting.
Automatic mode transitions need their own complete state/Undo coverage rather
than being bundled into an apparently read-only observation.

**No-MCP is not the cause of model round trips.** The Claude adapter uses
typed plans over its native CLI. Observation-dependent decisions need a
model round in either transport. Batching/local checks remove avoidable rounds
without reintroducing MCP or replacing established provider session engines.

**No new classifier inference on every request.** Keep stable schemas/prompts,
native context reuse and the user's selected account/model. Compact repeated
JSON-schema definitions without weakening runtime validation. Promote common
approved patterns into tested recipes, but never reuse prior script approval
as authorization to execute again or silently mine credentials/transcripts.

Validation
==========

Use independent host and factory-startup native tests for scene queries,
staged isolation, stale approvals, cancellation, persistence, preview pixels,
Undo/Redo, task postconditions and checkpoint restoration. Performance runs
must be separate from concurrent builds/tests. State measured results with
their method (``performance.rst``), never as unverified feature or speed
claims.
