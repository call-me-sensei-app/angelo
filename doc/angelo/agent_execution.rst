.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Native agent execution
**********************

Angelo keeps established Codex app-server and Claude Code sessions and dispatches
their admitted tools directly to Blender's main-thread executor. No MCP server,
computer-use loop, extra model router, or new provider session engine is needed.
The toolset version changes when contracts change so resumed provider sessions
cannot silently keep an obsolete tool surface.

Human workflow
==============

1. Choose a connected model in Coding Agent and describe the result. Provider
   credentials remain in AI Providers, not the editor. Accounts and billing
   policy are unchanged by native execution.
2. The agent can discover capabilities, inspect objects/dependencies, look up
   the installed RNA/BMesh API, and expand a deterministic recipe without
   running unrestricted Python.
3. A scene program declares its existing read/write objects, exact revisions,
   ordered actions and expected results (each object once: a ``write_set``
   entry is bound and revision-checked like a ``read_set`` one). Ask mode presents all canonical
   controls for review. Auto bounded mode can admit only the existing closed
   allowlist; Read only cannot apply a program or accept a preview.
4. During staging, chat reports actual progress and Stop cancels at a safe
   operation boundary. A native kernel already running is not forcibly killed.
5. PREVIEW returns an actual solid-shaded image and a structured change/check
   summary. The artist can review and Accept or Discard without changing the
   scene while comparing the result. Preview is not a final material render.
6. Accepted edits commit through one ordinary Blender Undo-enabled operator.
   Human Accept creates its own visible request/Restore boundary, distinct from
   the earlier preview request. Stale edits require a fresh observation and
   preview rather than overwriting the artist's intervening work.

Project Brief, Performance Summary and Snapshot Settings are available from
the chat controls. Brief edits and snapshot retention settings are human-reviewed
actions, not inferred permissions; the saved retention limits are then applied
automatically. See ``task_memory.rst`` and ``checkpoint_policy.rst``.

Contracts and ownership
=======================

``BlenderExecutor`` dispatches public tools and operation-specific preconditions.
``Controller`` owns consent, request identity, durable invocation receipts,
document binding, scheduling, progress, result recording and provider delivery.
An identical completed call can replay its actual receipt; the same ID with
changed arguments must not execute. A response serialization error after a real
edit remains a possible partial change with checkpoint recovery, never a false
"nothing happened" result or an automatic retry.

Detailed results can be evicted, but permanent mutation-ID protection cannot.
Each conversation retains at most 4,096 mutation identities; reaching that limit
refuses new mutations instead of evicting old protection. Start New Chat to use
an explicitly new namespace. Legacy histories without complete coverage, and
corrupt histories, remain readable but require a new chat for mutations. This
does not remove provider connections. Known IDs still reject changed arguments,
including attempts to reuse a mutation ID as an observation. The invocation
digest covers the tool name, so an ID recorded under a pre-rename tool name
(toolset 14 and earlier) is refused, never re-run, when it arrives with the
renamed tool; such chats also resume no native context
(``agent_integration.rst``).

``ProgramJob.poll()`` performs bounded units of staging, action and check work.
Cheap native actions coalesce into quanta of up to eight operations or four
milliseconds of action work, always allowing one operation to complete. Native
polls also fuse units: one hydration serves preparation, further quanta, each
expected check, stage validation and read-back while the poll's work stays under
16 ms, or under that poll's own hydration and suspension cost when it is larger
(at most 100 ms, ``program_jobs.fused_work_budget``), and every poll completes
at least one unit. Closed stages transfer
ownership through a private native Main between polls. Stages requiring a
private-bundle round trip also make the artist scene rebuild its dependency
graph, so a cheap program becomes ready in one poll instead of one per phase
and check.
Re-verifying exact revisions (accepting a preview) does not hydrate the stage by
itself, and neither does a poll that is already ready to commit. Each operation
retains its own budget checks and
ordered result; Stop takes effect between polls, not mid-kernel.
Hydration/serialization (never counted as work) and a single long kernel can make
a poll longer than its work budget.
``poll()`` never commits.
``ready_to_commit`` is an intent, not success. Only the host's Undo operator
calls ``commit()``. At most one cooperative program owns the writer lease across
all chats. A chat whose scene is not shown (``document_scene.hold``) keeps no
lease another chat waits for (``document_scene.yield_writer``): an ordinary
stage is discarded, nothing live having changed, and the same approved program
stages again once its scene is shown; an accepted preview was consumed and never
stages again, so it keeps its exact stage as its last poll suspended it (no Main
ID), gives the lease up, and once shown takes it back and runs REVALIDATE and
every approved check again before it commits, failing rather than committing
anything else when the live scene changed meanwhile
(``program_job_state.suspend_accepted_writer``). A stage stopped off the main
thread (a voice session's end) waits in the chat's deferred cleanup, which its
next pump retires on the UI thread whatever scene is shown
(``pending_delivery.retire_native_cleanup``). Read-only queries stay available, and background provider/I/O workers
never access ``bpy``. The UI timer admits bounded work and yields between phases;
this is not a hard real-time latency guarantee for every supported kernel.

Programs contain 1–32 canonical actions, up to 64 distinct existing input objects
and up to 32 expected checks. Missing checks do not invent task success: stage
validity is still checked, but only explicitly supplied predicates certify an
intended result. New objects use local ``$alias`` references. Existing targets
use document-epoch handles plus observed revisions, not names or copied UUID
custom properties. All affected existing objects must be in the write set, and
all mutable dependencies must be declared and isolated. An object dependency
of a bound input (its parent, a constraint, modifier or driver target, a
camera's focus object) that the program does not declare is bound to the
revision the context's latest observation showed for it, as inferred bindings
are, and is staged, fingerprinted and verified again before commit like a
declared read; one the context never observed, or that changed since, is
refused. A failed expected check returns its reason or what it observed. A
refused operation is named first, ``operations[i] (kind):`` with its index in
the program, its code and details kept (``program_spec.operation_error``), and
a missing field by name (``... is missing required fields: target``), so a
model never has to guess which operation and field to correct. A creating kind
given a target is told to leave it out.

Approval timeouts and turn end
------------------------------

One table, ``tool_deadlines.tool_seconds``, gives how long a native call may
wait for the person: 180 s for every call (queueing, preparation and the click),
600 s for ``angelo_execute_python`` (its exact code is read first), and 1,500 s
for Studio generation and Flow runs (a started job then sets its own, longer
bound). ``Controller.request_tool`` takes its call's expiry from that table and
``expire_tool`` answers an unanswered call on the UI tick.

A provider waits on that same answer, not on a number of its own: a flat wait
shorter than the call's expiry would end the whole turn while a 600 s approval
was still on screen. ``await_native`` polls until the call is answered or its *current*
expiry (``RuntimeContext.tool_expiry``, which follows a started job's longer
bound) plus 15 s have passed; only a UI thread that stopped ticking reaches that
backstop, and it reports "Native Blender approval/dispatch timed out".
Codex answers each call asynchronously and has no per-call wait. While the host
holds a call of a Codex turn, the turn's own clocks do not run (`How long a turn
may run`_); a turn that ends for any reason revokes any call still waiting
(below), so a script approved after that point never runs.

**A turn that ends revokes what it admitted.** ``RuntimeContext.emit`` of the
worker's ``finished`` event first marks every call of that request that is not
already running or committing as revoked (``revoke_turn_calls``: a field write
under the host lock, no Blender data). From then on ``expire_tool`` answers the
call with "The agent turn ended before this was approved; nothing was run"
(``revoked: true``) at every place execution could start: each of those calls
``_tool_available`` (pinned by an AST test), which asks ``expire_tool`` first. That
covers the Approve and Apply operators, ``approve_tool``, ``execute_tool``, the
staged commit and the tool pump (which expires a revoked call before its Auto
bounded branch). Without this, a call would stay approvable from
the moment the worker returns until the UI thread handles ``finished``, and a
click in that gap would run the script with nobody waiting for its result. A
running call is never interrupted. The last start check runs on the main
thread without the lock, so the start itself is atomic with the revocation
(``tool_deadlines.started``, under the same lock): an execution's
``executing``, a commit's ``committing`` and a typed apply's
``mutation_started`` are set only if no revocation landed since that check.
A revocation in between then refuses the call (nothing ran) instead of being
marked on a call that runs anyway; one after it finds the call started. Every
chat, window or hosted, shares it. Tests: ``test_native_tool_deadlines.py``,
``test_native_tool_deadlines_start_gap.py``.

Pending readers and monitors also carry irreversible delivery ownership
(``request_fences.DeliveryOwner``). A request closes its owner before queuing
``finished``; a Codex dynamic call additionally belongs to its exact native
turn and transport. The main-thread pump closes an obsolete monitor before
polling it again. The pending object's own stopped result describes work
already submitted; an actual live mutation keeps its durable result. Local
plan execution after a Codex native turn ends retains the separate request
owner, and each successive voice turn gets a new native owner.

Delivery is checked again before image packing and before the original RPC
response. No provider lock covers capture, packing or a pipe write: a phase
already admitted may finish, while a closed owner admits no subsequent phase.
This is not cancellation of bytes already being written or proof that a model
received pixels. A Codex call that arrives after its owner closed (or a
deferred one replayed after its owner was superseded) is never admitted, but
it is answered with ``codex_delivery.STALE_CALL`` (``success: false``) on the
transport it came on, unless a live call holds that RPC ID. codex app-server
waits for every ``item/tool/call`` reply: in the unbilled
``codex_stale_call_probe.py`` (codex-cli 0.162, a local endpoint), an
unanswered call kept its turn open until the code-mode cell yielded about 30 s
later, and the next ``turn/start`` on the thread was steered into that turn
instead of starting a new one; a prompt refusal let the turn finish after one
more model request. After ``turn/interrupt`` (Stop) Codex cancels the waiting
call itself and ignores a late reply. codex-cli 0.163 also completes that
call's item as failed (``dynamicToolCall``, ``status: "failed"``,
``success: false``) and sends ``serverRequest/resolved`` before ``turn/completed``
``interrupted`` (openai/codex#51556); the request ends interrupted at once and
Angelo writes no reply for the call (``test_native_codex_turn_errors.py``,
``Codex163TurnEndTests``).

**Concurrent calls (codex-cli 0.164, not handled yet).** Up to 0.163 Codex runs
one dynamic call at a time. 0.164 lets a code-mode cell's ``Promise.all`` send
several ``item/tool/call`` requests of one turn before any is answered
(openai/codex#54654). Before Angelo bundles a runtime with that change, these
places need a concurrency review and tests with overlapping calls:

* ``codex_delivery.dispatch_dynamic_call`` and ``CodexTurnScope``
  (``claim_response``, ``begin_response``, ``retire_response``): one response
  ticket per RPC ID is assumed safe for several outstanding IDs, but untested.
* ``CodexProvider._turn_calls`` and ``codex_plan.emit_summary``: calls are
  recorded in arrival order, which is no longer the model's order.
* ``Controller.request_tool``, ``Controller.pending`` and ``ToolDispatchBudget``:
  native operations stay one per UI tick on the main thread, so overlapping
  edits run in arrival order; two edits that depend on each other were written
  for sequential calls and need a stated rule (refuse, or serialize by the
  model's order).
* Approvals: two calls of one turn could wait for approval at once, which no
  test covers yet.
* ``request_path.record_codex_result`` and ``query_observations``: per-call
  observations and identifier tables updated from overlapping results.
* ``codex_turns.OpenTurns`` is per turn, not per call, and needs no change.

``codex_turns.OpenTurns`` records which turns Codex still has open on the
connection, whoever owns them: a turn opens when ``turn/start`` returns it, a
``turn/started`` notification or one of its tool calls names it, and closes on
``turn/completed``; a replaced or disconnected process forgets them. Voice
turns are not recorded. Three paths use it:

* a refused stale or foreign call also interrupts its turn when that turn is
  still open and is not the current turn of a current owner, so it ends at once
  instead of making another model request;
* an admitted call whose owner closed without Stop or an overdue interrupt
  (the request ended, for example after a late ``turn/start`` reply) sends no
  reply and no refusal; its turn is interrupted, Codex cancels the call, and the
  durable host result states what actually happened (run, refused or stopped).
  Stop marks its own turn as interrupting, so no second interrupt is sent;
* every ``turn/start`` goes through ``OpenTurns.start``: it interrupts any turn
  still open on the thread and waits for its ``turn/completed``, bounded by
  ``SETTLE_SECONDS`` (15 s). If a turn never completes, the connection is
  closed (a new app-server process has no open turns; the next request
  reconnects) and the request fails with ``codex_turns.STUCK`` without sending
  the message. On an app-server shared by a connection's chats the record
  belongs to the process and the stuck thread is abandoned instead: the request
  fails with ``codex_turns.STUCK_SHARED`` and the chat's next message opens a
  new context with its handoff (``agent_integration.rst``, "Shared Codex
  app-server and forked openings"). A new human message is therefore never steered into an older
  turn, and a dead turn cannot block the chat. Stop during that wait ends it at
  once (``interrupt_ids`` wakes it) and abandons the send
  (``codex_turns.TurnStartAbandoned``): the message is not sent, the request
  finishes as interrupted, the warm connection stays open and the older turn's
  interrupt stands. A request already stopped before ``turn/start`` sends nothing.
  Whenever a request ends without sending its message (these two cases,
  ``codex_turns.MessageNotSent``), its ``finished`` event says ``not_sent``: the
  chat adds the translated notice "Your message wasn't sent. Send it again when
  you're ready." instead of an English error, puts the person's references back
  if the composer has none, and ``drafts.restore_unsent`` returns the text to an
  empty input (or to that chat's saved draft); text the person has started
  typing is never overwritten.

The probe sends its second message through that guard and gets a fresh turn ID
in every case; ``--raw`` reproduces the absorbed message.
Delegated children retain their exact parent admission and
helper lifetime; replacing a parent with the same key grants no ownership.
``pending_delivery.py`` owns the transient ``ToolCall`` dataclass, re-exported
by ``controller.py``, and the cleanup and once-per-admission outcome observer.
Future call fields belong to this single dataclass. ``codex_delivery.py`` owns
the dynamic-call callback and response phases; provider session/event routing
stays in ``providers.py``. A pending RPC claim suppresses overlapping envelopes.
During its admitted response write, the same logical ``callId`` is still a
duplicate; a distinct ``callId`` may reuse the RPC ID when the peer observes the
answer. The exact old claim retires when the write returns, before accounting,
and cannot remove a successor claim. Later reuse of the same logical call follows
the existing invocation replay rules. This is a response-phase boundary, not a
physical-delivery acknowledgment. Joined delivery callbacks do not recount a committed edit. The final receipt has its own owner check, and
closed helpers settle before their children can be polled again.
``test_native_pending_delivery.py`` covers these host boundaries without a
native renderer or provider process.

Correction after a changed scene
--------------------------------

Refusal results carry a closed ``error_code`` (``refusal_codes.py``); error
wording never grants retry authority. A failed program's result also names the
operation that failed (``failed_operation``: its index and kind, only when the
failure came from applying it, never from a check between operations) and, at a bound
(``LIMIT_EXCEEDED``), what works instead (``instead``: the script route, or
splitting a per-operation or per-program bound); the program is still refused
whole (``program_job_state.failure_facts``). Rule-based face and edge selectors
match up to the typed programs' mesh limits (200,000 faces or edges); 4,096
bounds only explicit
index lists, whose length is part of the arguments. Known validation, unsupported-operation,
budget and expected-check refusals may use the existing bounded correction
rounds only when no live write or unresolved cleanup occurred. A kernel's
deliberate refusal while a program binds or stages (a missing material, a 33rd
slot, a taken batch name) raises the exact ``RefusalError`` type with its code
at the raise site, as ``action_nodes`` always did: native preparation trusts
only that type, and a plain ``ValueError`` would read as ``UNKNOWN``, raise the
barrier below and block every later edit of the request although nothing live
was written. An internal fault the host can name (a
material that staging did not copy, an effective slot that did not take its
material, an unknown material owner while obligations replay, a binding left
unresolved) raises ``INTERNAL_ERROR``, which raises the barrier unless the
program was only staged and its binding reads back unchanged (below); a linked
duplicate's data edited later in the program is ``VALIDATION``. Only argument
validators that run at admission, where the controller answers ``VALIDATION``
before anything runs, and one named invariant keep plain exceptions;
``test_native_stage_refusal_codes.py`` fails on any other. The same refusal
when commit resolves the binding again (``verify``) is ``STALE_DEPENDENCY``:
the program resolved when it was bound, so the live state changed meanwhile;
an ``INTERNAL_ERROR`` there keeps its code and the barrier. Unknown and
internal failures do not claim ``scene_changed: false`` and cannot silently
start another scene mutation in the same request. A classified failure that
wraps an unclassified cause follows the same rule: a staged program whose
hydration failed and whose cleanup then failed reports ``CLEANUP_FAILED`` with
the cause in ``refusal_details.cause_code`` and, when that cause is unknown or
internal, omits ``scene_changed``, because nothing on that path read the live
fingerprint. The request barrier reads that cause too: an unknown or internal
``cause_code`` raises it as an opaque change, exactly as the bare code does.
A failure while the job resumes its stage, for a poll or for commit, before
any write, is verified rather than assumed: the job re-reads the bound revisions after
cleanup and, when they hold, reports ``scene_changed: false`` with
``rollback_verified`` (``program_job_state.resumed_before_writes``; a failed
cleanup keeps its own partial changes).

An unknown or internal failure while the program is only staged (copy, remap,
operations, validation, read-back; no live write started, no cleanup pending,
and the error claims no partial change) is verified the same way and, when the
bound revisions hold, is never a dead end: it becomes an ``UNSUPPORTED``
refusal (``program_job_state.staged_refusal``) that names the operation, the
engine's reason, that nothing changed and what to change, with the engine's own
code and reason in ``refusal_details`` (``engine_error``, ``engine_reason``).
It raises no barrier, so the model corrects the program as for any refusal. A
parallel-edit worker's turn ended this way (2026-10-25: a material appended
after a Boolean apply). A failure at bind, at commit, or one whose bound
revisions do not read back keeps its code and the barrier. A material
appended (slot -1) after an earlier operation changed its object's slots (a
Boolean apply adds an empty slot for its operand) is refused before it runs
(``VALIDATION``, naming that operation): the planner places the material from
the slots it knew, and the native operation appends at the count it finds. A suspended stage records the
Geometry Nodes parameters only of the graphs its scene still uses, so a graph
a program applied or removed does not fail that resume (for example a
Geometry Nodes apply beside a World edit).

A refusal of a scene edit can open a request-owned recovery
(``request_recovery.py``). The model decides what to do next; the host's part
is full and correct visibility, and a hold only where an edit may have run:

* **Nothing changed.** A stale refusal whose own result says so
  (``scene_changed: false`` with no partial change, started mutation, cleanup
  or rollback failure, ``request_recovery.unchanged``), or one refused at
  commit preflight whose typed edit was never applied although Blender's
  preparation callbacks ran (``not_applied``; its note stays), holds nothing: other
  edits, scripts and jobs run as usual. Its ``recovery`` names the targets that
  changed under the edit (``read``) and the tools that read them; a second
  such refusal adds its own. A Claude or Codex plan gets a corrected plan for
  it (``claude_outcomes.correctable_failure``), and a corrected edit binds what
  a later read or the correction's observation showed. Holding such a refusal
  would leave the request waiting on reads that cannot satisfy it (the host may
  never have captured those objects) and refuse its later jobs.
* **May have run.** A stale refusal that does not say nothing changed, every
  unclassified or internal failure, and any refusal that says the scene may
  have changed (``may_have_changed``: a live check that failed after writes
  whose rollback failed) hold every scene mutation of the request
  (``RECOVERY_REQUIRED``) until each target has been read again: the named
  ones and the edit's write set, from its arguments or from the program it
  staged (a preview's acceptance names only the preview). Admission, already
  queued calls, staging and commit all check the hold under both runtimes. A
  render job is not held: it changes nothing in the scene; bakes and exports
  are. Only a read the model receives counts, and only for what it shows: a
  detail, a search row, the scene settings, a turn observation's row, or a
  read that finds the target deleted or expired. A capture the host took
  without showing it (a search scans every object) and a collaborator's read
  count for nothing. Once every target is read, scene mutations of that same
  model step are still refused, carrying what changed, and run from the next
  step: a read's result can go to code the model wrote before it saw it (a
  Codex exec cell that reads and then edits). A step ends with each usage
  report of the lead's Codex turn and with its turn's end (``provider_usage``;
  Codex sends the report only after every tool call of that response, an exec
  cell's included, has resolved), and with each Claude round, submitted after
  the previous round's plan ran (``Controller.record_model_round``); a
  collaborator's never ends one. The completing read fixes the step: later
  reads never push the wait back. The check has no side effect a refused call
  could use up, and its text names the targets still to read (a plan's
  rejection quotes only the text), with the tools that read them, and never an
  empty list: a hold that names no target says that scene edits wait for the
  next request. An unclassified failure, or a stale one naming no target, has
  nothing a read can explain and holds until the next request (the person's,
  or Angelo's own continuation when workers end), with read tools available
  and the pre-request checkpoint restorable; a classified failure naming no
  target keeps its own recovery text. Every tool a recovery, or a refusal's own
  prose, names reaches the model under the name it calls
  (``recovery_reads.callable_name``, applied at delivery by
  ``recovery_reads.named_for``): a Codex thread on OpenAI calls a deferred
  family's tool as ``tools.more__<name>`` in code mode; voice threads and
  Claude's routes use the plain name. Recorded (C3 early town-centre
  diagnostic): a target-less hold read "Read again: ." and its hint's
  ``angelo_scene_settings`` was not callable in code mode.

Either way, the read that completes the targets carries ``changes``: each
field with complete values that differs from the host's earlier capture
(before and after), the fields only a digest records (``unexplained``, an
empty list when only the revision moved), and the targets the host had no
earlier capture of (``not_captured``) with the values their fresh capture
holds (``current``). Existing observation reads retain at most 64 private
records and 256 KiB per request, including exact field hashes and the
complete values already available for transforms, camera/light settings and
allow-listed scalar scene settings; the value rows are bounded at 64 and
16 KiB (``changes_omitted`` counts the rest). There is no additional scene
traversal and no data in ordinary model prompts. The records clear at the next
request or when the controller closes.

Nothing in a recovery grants authority: a corrected edit binds the revisions
its own reads or observations showed, and the native owner still compares
exact preconditions at bind and immediately before live writes, so a stale
binding is refused again. The correction is approved under the chat's own
approval mode. The ``recovery_observation`` field stays in the schemas so
saved chats and toolsets validate; it grants and requires nothing.
``test_native_recovery_hold_paths.py`` pins the hold's paths. Known limits: a Codex exec
cell that yields (``yield_control``, its yield pragma or the default 30 s) keeps
running into the next step, so its later edit is admitted; and a provider that
reports no usage ends a step only at the turn's end. Both need the bundled
runtime to name a call's exec cell, or to report every usage count, in its
tool-call parameters. Also: an exec cell whose code drops a result the model
never prints (no host can show it more), a call admitted before the hold that
waits across steps, objects the edit created (the hold says a search lists
them), and add-on handlers a render runs. A failed or missing Undo publication reports failure, unverified
recovery and ``undoable: false``; it does not erase applied edits or claim a
rollback. Verified frame-only rebinds and bindings advanced by the request's
own committed operations keep their existing paths.

How long a turn may run
-----------------------

The host waits for a runtime as long as it shows life (``turn_activity.py``),
for both runtimes. A flat limit would end a round that has streamed its
reasoning for ten minutes exactly like one that never answered, and on Codex it
would also count the minutes a person spends reading a script. The host gives up on a
turn only when

* nothing has come from the model for 600 s (``IDLE_SECONDS``): a hung process
  or connection. Every event of the model's answer counts (the API's stream
  events, thinking included, not only text); the runtime's own notices, such
  as a retry, a keep-alive or the ``ping`` Claude Code puts into a quiet
  stream, do not; or
* one stretch of model work has lasted 1,800 s without a result
  (``STRETCH_SECONDS``): a runaway response. A Claude round is one stretch. A
  Codex turn starts a new stretch whenever the host hands back a tool result.

While the host holds a call for a Codex turn (``RuntimeContext.tools_pending``:
waiting for approval, or running) neither clock runs; each call has its own
expiry, above. Effort is not an input: a stream of events says directly what a
higher effort could only suggest, and a runtime that sends nothing looks the
same at every effort. Stop works throughout. The error names which limit was
reached ("nothing came from the model for 10 minutes", "one stretch of model
work ran for 30 minutes without a result"). ``test_native_turn_activity.py``
runs both limits against the real Claude transport reader and
``test_native_turn_activity_codex.py`` against a scripted Codex app-server,
with the limits scaled down.

A message that keeps streaming
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A stream that never stops resets the silence clock with every delta, so on its
own only the stretch limit would end it. On a Codex plan thread
(``codex_plan.py``) the host therefore follows each streamed message
(``plan_runaway.py``) and cuts the turn when one passes a ceiling:

* 32 KiB of consecutive whitespace, or 8,192 consecutive deltas of nothing
  else, outside any JSON string of a message that is JSON so far (a
  constrained JSON response can emit whitespace without end; whitespace there
  adds no structure, and the delta count ends a stream of one-character tokens
  in about two minutes at the recorded rate);
* 256 KiB of message (UTF-8); or
* 600 s since the message's first delta.

The waiter (``turn_activity.wait``, ``CUT``) stops the turn and records it as
interrupted (its ``turn/completed`` records what it used, once, even when Stop
arrives meanwhile). Nothing of the message runs: a plan runs only after
``turn/completed`` and Codex keeps no partial message. When no dynamic tool
call of that turn reached the host, the turn is resent once, sharing the
request's one resend (``codex_retry``), with a host note that says what
happened, and the chat shows that Angelo stopped the reply and asked again
(``provider_retries_runaway`` on the receipt). Otherwise the request fails with
a translatable message that says nothing in that reply ran and that earlier
edits stay with Undo. Codex 0.163 sends no ``max_output_tokens`` with its
Responses requests, so there is no runtime ceiling to set instead.

*Why these values.* A recorded gate turn (GPT 6.1 Sol, low effort) streamed one
plan message at 64-80 deltas a second for 297 s without finishing, against
38-100 output tokens in a normal turn; one in about 1,650 Codex gate tasks. The
ceilings were set from 5,492 saved Codex rollouts (gate, benchmark, A/B,
head-to-head, scene and sculpt runs): 14,697 plan messages and 3,353 other
agent messages from GPT 6.1 Sol and GPT 6 Astra.

==================  ============  ===========  =========  =========  =======
Measure             Samples       p99          p99.9      Max        Ceiling
==================  ============  ===========  =========  =========  =======
Plan message size   14,697        1,721 B      4,438 B    9,572 B    27x
Time to finish [1]  14,697        11 s         20 s       41 s       14.6x
Whitespace run [2]  14,978        —            2          3,015      10.9x
Blank deltas [4]    14,978        —            2          <= 671     12.2x
Message tokens [3]  14,697        503          1,428      3,179      —
==================  ============  ===========  =========  =========  =======

[1] From the turn's previous recorded event to the finished message, an upper
bound on streaming time. [2] Characters outside JSON strings; the 3,015-tab
run was inside a Sol plan that then finished. [3] The response's output tokens
minus reasoning tokens, an upper bound on the message's deltas. [4] Deltas
holding only whitespace outside strings; the 3,015-tab run's whole message was
671 output tokens, so the run took at most 671 deltas.

What the corpus lacks: almost all samples are low effort (28 plan messages at
medium, none higher), and the tasks are edits, so a long written answer (which
a plan's ``reply`` carries) is not represented; the size and time ceilings
leave room for about 64,000 and 42,000 tokens of reply. Claude plans are not
covered here: Claude Code bounds each response by its own output-token limit.
``test_native_plan_runaway.py`` replays runaways and the two healthy extremes
of the corpus (``plan_runaway_corpus.json``).

What a person sees during a long Claude round comes from the events Claude
Code forwards (``claude_stream.py``): a thinking block opens a thinking row
whose text is the summary the runtime streams (nothing is invented, and a
runtime that streams none still shows the row and its duration), and when the
``StructuredOutput`` call that carries the plan begins, the live row changes
from "Thinking" to "Preparing the next steps" until the plan arrives. Without
these events a Claude round would show "Thinking" and nothing else until its
plan is complete; Codex rounds show their reasoning summaries. Replayed
through the real transport reader with a stand-in runtime (a round that starts
to think 0.2 s after the message and delivers its plan at 9.2 s, five runs
each), the first event the chat can show comes at 0.21 s, with the thinking
row at 0.25 s, instead of at 9.26 s with the plan. The stand-in shows what the
host does with these events, not how soon a live runtime sends them.

Claude Code can stream a tool call's input as the model writes it
(fine-grained tool streaming, ``CLAUDE_CODE_ENABLE_FINE_GRAINED_TOOL_STREAMING``;
an inherited value is dropped, ``runtime_environment.STREAMING``). On the
Anthropic API it does so only while Claude Code's own server-side flag for it
(``tengu_fgts`` in 2.1.293) is on; otherwise, and on gateway connections, the
API buffers each input field, and the plan's reply and its progress line
arrive only as each field ends: a reply at the end of the plan. While it
streams, the plan's own text reaches the host as it
is written:

* Its top-level ``reply`` streams into the chat row (``claude_stream.PlanReply``):
  only that key, never a ``"reply"`` inside an operation, with each escape
  decoded once complete. The plan schema lists ``operations`` first and the
  model writes it first, so an answer (operations ``[]``) starts about twenty
  characters into the plan, while an edit's reply, when it has one, still
  comes last. Until the provider's final row for the plan the host received,
  the streamed text is drawn and never stored (``reply_overlay``, keyed by the
  request generation): the transcript, its saves, handoffs and rebases never
  hold it. That row writes the plan's reply, or leaves the row as it was when
  the plan has none; a round that fails, is refused by the runtime's schema
  check, superseded or stopped ends with the request and its text with it. A
  later plan call (a schema retry) or round starts its text over. The text is
  redacted whole each time it is drawn, and a tail that could begin the
  connection's key waits for the next fragment. The row recording each plan
  call (the runtime's own record, with its schema verdict) keeps the call's
  operations but never its reply, so a refused or superseded plan's reply, or
  a key it repeats, is in no save and no opened row either. What runs, and its
  validation, are unchanged. ``test_blender_reply_stream.py`` proves it in the
  built app's chat, with the stand-in ``claude`` CLI streaming each plan as
  fine-grained tool input: the answer grows between two frames without being
  stored; a refused, stopped or withdrawn round leaves no reply row on screen
  or in the save, and a withdrawn round's row shows the earlier round's reply
  again; a key split across fragments is never drawn, nor is a key in a
  plan's operations when the plan call's row or that operation's row (opened
  from the work group it folds into) is opened.
* The live row's detail names what the plan makes (``plan_progress``): the
  objects its programs create, and a reviewed script by its own
  ``description``, which the model writes before the code.
* Under the live row the plan's steps appear as they are written
  (``plan_steps``), each with a "Planned" chip and never a done one: each
  operation of its programs by its own name (an object's, a material's, a
  modifier's) or by its exact action, a script by its description, another
  edit tool by its title; copies of one thing are counted under their stem
  ("Post ×4" for ``Post_FL`` to ``Post_BR``), and a ``batch`` of one created
  unit is counted on that unit's row once the batch is complete (its
  ``batch_spec.instance_count``, read from the whole operation by
  ``plan_ops``: "Post" becomes "Post ×4"); a batch of several sources shows on
  its own. Steps never wait for a trailing field, so a Codex call that ends
  early (a recipe after its ``mode``, a transform after its ``scale``) shows
  as soon as it starts. Lookups, views and checks are no steps. A value is shown only once its closing quote has arrived and is cut
  only at a space, so a fragment boundary never shows part of a name and a
  key (which has no spaces) is whole or absent; the controller redacts the
  status whole, so it reads ``[REDACTED]``. The first step is shown at once,
  later changes at most twice a second. The rows ride on the status after its
  detail, so they last exactly as long as it: a plan call the runtime's schema
  check refuses takes them away at once (``Thinking`` until the model's next
  call), a later plan call starts them over, a Codex plan message takes them
  away when it is complete, and the round's result, Stop or the next request
  ends them in any case. They are drawn only: no transcript row, save,
  handoff or model input holds them, and a step becomes a real row only when
  the host runs it. Replayed at their recorded pace, each of 676 recorded
  Claude plans with operations shows a row, the first a median 0.49 s into
  the plan (0.74 s at the 90th percentile); the names detail alone named
  something in 81 of them, at the same moment. The other 595 were short edits
  (a median 0.94 s of plan) that showed only "Preparing the next steps" until
  they ran. What the person waits for before any of this is the plan's start
  (runtime start, the first token and any thinking whose text Claude Code
  omits), which no reading of the plan can shorten.
  ``test_native_plan_steps.py`` decodes a recorded plan at every
  fragment boundary and pins the refusal, supersession, Codex and key cases;
  ``test_blender_plan_progress.py`` times the first row in the built app
  against the stand-in's own stream and screenshots the rows (English and
  Arabic), a refused plan, Stop and a keyed step.

A tool call's row (``role: activity``) is the transcript's copy of the call.
Its input, its result and every other field its events carry pass through the
chat's own redaction (``Controller.redact``) before the row is stored
(``activity_rows``), so a key that a script, a plan's operations or a runtime's
verdict repeats is ``[REDACTED]`` in the row, its saves, a handoff built from
it and its opened details, on the Claude and Codex paths alike. Binding a key
(``Controller.api_key``) also redacts it from rows a chat saved before. The call
runs with what it was given, and what the model receives in the turn is
unchanged (``finish_tool`` already redacts the result it returns). The Stop
notice says what Stop does to the work in flight (``stop_notice``): previews
and native jobs stop between steps, a running script cannot be interrupted, and
a request with neither (a reply streaming, thinking, a call still queued) is
simply ``Stopped``. ``test_native_activity_row_redaction.py`` pins both.

Estimated from recorded transcripts (block timestamps; plan text written at
a median 273 characters a second, from the 7 answers with a thinking block),
an answer's first words come 0.3 s sooner on a chat's opening question
(median 3.07 to 2.73 s from sending, runtime start included) and 0.78 s sooner
on later questions (2.08 to 1.28 s). Not yet measured live; the receipts'
``first_text_ms`` records it. A streamed reply fragment never starts a
conversation save (it is display only). Opus 5.5's thinking blocks carry
no text in these sessions (Claude Code asks for thinking display ``omitted``
in print mode), so the thinking row shows its duration but no summary.

Showing a summary must not cost the main thread what a reply costs. Summary
text reaches the chat at most twice a second (``claude_stream.THINKING_SECONDS``),
and a status or thinking event never starts a conversation save
(``tool_finalization.DISPLAY_EVENTS``): a save appends and syncs a record on
the main thread (`The conversation journal`_), and the event pump otherwise
saves every half second while events arrive. The row is saved with the next
durable event or at the end of the turn. This also holds for Codex reasoning
summaries.

A provider thread that admits a tool call saves its journal record holding the
controller lock, and the event pump changes transcript rows only under that
lock, so no save can encode a row while the pump changes it. A pump that meets
such a save waits for the rest of it. An append costs about 4 million
instructions (well under a millisecond, ``fsync`` included) at any length of
chat, against 26 ms after 500 edits for a save that rewrites the whole journal
(``performance.rst``, "Per-edit cost over a long session"). A save that must
rewrite the whole file (the first after a load, or after ``invalidate``: a new
key bound, a restore's badges, a failed write) costs a whole-file write: on a
2.5 MB chat the main thread waited up to about 21 ms (p99) when one save in
ten was such a rewrite (``journal_review/lock_wait.py``).

The conversation journal
------------------------

Each chat's transcript and its permanent mutation-ID ledger are one private
file, ``sessions/<id>.json`` (``SessionStore``). A save appends what changed
since the last save instead of rewriting the file (``session_writer``); the
format and its recovery are in ``session_journal``.

* **Format.** A *base* line holds the whole conversation as one JSON object:
  the document every earlier build wrote (``"version": 1``), with the rows
  last. *Records* follow, one JSON object per line: ``{"seq": n, ...}`` with
  the fields whose cleaned value changed (``set``, ``unset``), the rows
  replaced or appended (``rows``), the result records added, replaced or
  evicted (``results``) and the mutation IDs added (``ledger``). Every part is
  a new value, never an increment.
* **What a save encodes.** The cleaned fields (the task memory is cleaned again
  only when it is another object: its helpers are pure and install a new one);
  the rows of the current request, from its first row and at most the last 64;
  older rows the controller changed in place (``SessionStore.touch``: replies,
  activity rows, thinking rows and checkpoint notes, which can arrive after
  their request ended); and eight older rows in turn, a scrub that brings any
  change no caller marked to the file within a bounded number of saves. Result
  records are compared by identity: every phase stores a new record and
  ``complete_result`` replaces one rather than changing it. The mutation-ID
  ledger only grows: the entries added since the last save are taken from its
  end and validated as they are written (``invocations.valid_entry``), and the
  whole ledger is validated only on a load or a rewrite.
* **Rows change only under the chat's lock** (``Controller.lock``), which every
  save holds: appends and inserts (the event pump, ``_append_row``, a finished
  job's notice in ``job_ui``, a restore's notices) and changes in place (a
  restore's outcome and badges). A save encodes its rows one at a time and other
  threads run between them, so it reads the row count once: a row another thread
  adds meanwhile goes to the next record whole, never counted without its bytes
  (a provider thread's admission save racing a job's notice wrote a record
  whose count did not fit, which loaded as corruption).
* **Durability is that of the whole-file save it replaced.** When a save
  returns, the state it was given is on disk and synced. An append writes its
  record and newline with one ``write`` on a descriptor opened ``O_APPEND``
  and ``O_NOFOLLOW``, under an exclusive ``flock`` on POSIX, and syncs the file
  (``fsync``, as the whole-file save did; not macOS's ``F_FULLFSYNC``, which
  waits for the drive's own cache and takes milliseconds per call on the main
  thread and under the lock) before returning. Strict saves
  (admission, execution and commit start) still precede native side effects
  and raise on failure; best-effort saves still deliver their result with the
  journal warning. A value that cannot be saved raises before anything is
  written.
* **Crashes and torn records.** Each record is synced before the next is
  written, so a crash can tear only the last one. A load ignores a last record
  that lacks its newline or is not valid UTF-8 JSON: its save never returned.
  Any other malformed record, a sequence gap, or a record that does not fit
  the document (a row past the end, an unknown part, a mutation ID added twice)
  is corruption, which loads as unverified history with mutation IDs closed,
  as a corrupt whole file always did.
* **Rewrites (compaction).** The base is written to a temporary file of its
  own (``<id>.json.<12 random hex>.tmp``, created exclusively, so two writers
  never share one; short, as a worker's deepest path counts on Windows; a crash's
  leftover is removed by the next load), synced and swapped in with
  ``os.replace`` (no directory sync). A rewrite happens on the first save after a load
  (which also drops a torn last record), when a chat closes, at the end of a
  turn once the records outgrow the base (and at least 1 MiB), on any save past
  four times the base (and 64 MiB), after ``SessionStore.invalidate``, after a
  write that failed part way (an append is first cut back to its old size), and
  when a row was inserted or removed before the current request or the row list
  was replaced.
* **A switch to another chat leaves it one base line** (``SessionStore.leave``,
  ``ui.show_session``). When the file already is one holding the chat, nothing
  is written: the check is an append's comparison (0.17 ms on a 2.5 MB chat of
  1,310 rows, against 14.6 ms for a rewrite). The
  switch's time (``updated``) then reaches the file with the chat's next save or
  its close; the switcher's order, which the index holds, is unchanged. A chat
  with records or an unsaved change is saved, then compacted from the bytes the
  file already holds, as at a turn's end (5.1–5.3 ms median there). A chat
  closed (``Controller.close``: chat close, add-on unregister, quitting) is
  always rewritten from memory.
* **Secrets never stay in an earlier record.** Binding a new API key redacts it
  from every row and every saved result record (``activity_rows.bind_key``).
  The next save then rewrites the file without the records that held it when
  memory held the key, and also when it did not but the file is not exactly
  memory's state: an earlier record can hold what memory no longer does (the
  head of a thinking summary cut to its last 16,000 characters, an earlier
  version of a reply, a result record evicted from the 256 kept). A file that
  is one base line holding memory's state (``SessionStore.current``) holds no
  piece of a key memory does not, so a chat just opened (its first save
  rewrote it) is not rewritten a second time when the connection's key is
  bound (``ui.get_controller``); binding the same key again changes nothing.
  The chat's kept copies are redacted too (``session_kept.redact_kept``): only
  the key's JSON-encoded bytes change, inside the string that held them; a copy
  is written back atomically and synced (its own temporary file, ``fsync``,
  ``os.replace``, then the folder), only when it still holds as many JSON values
  as before (every line counted, also past a damaged one), and never in place,
  so a copy that is still a hard link of the chat file leaves the chat file to
  its next save. A copy that cannot be redacted is reported on standard error
  by its name, never the key or the folder. A reply or a
  thinking summary is redacted over its whole text as it grows, so a key split
  across fragments is caught; when that redaction reaches text an earlier save
  may have written (the key's first pieces), the journal is invalidated too
  (``TranscriptRows._redacted_text``). A restore's badges on old rows invalidate
  as well.
* **One writer.** A save appends only to the file it last wrote: the same
  device, inode, size, modification and change time and one link. Anything
  else makes the save rewrite the file, and every store (a lead chat's or a
  worker's) rewrites only over the file as it last read or wrote it
  (``session_guard``): the identity it recorded, or the same file with the
  same bytes (CRC-32) whose times, mode or links changed. Another writer's
  content (another app on the same profile, an earlier build's save, a
  restored file, a link put in its place, an in-place edit) raises
  ``JournalTaken`` and nothing is written until the chat loads again, because
  a rewrite from memory would erase that writer's records. The store's own
  failures never read as another writer: a failed append is cut back under the
  lock and the file as it was left becomes the expected one, and a rewrite
  whose replace was refused with the file unchanged keeps the writer appending
  (the compaction is retried when next due). A rewrite whose own stat after the
  replace failed takes the file's identity from a stat then, if the file holds
  exactly the bytes it wrote, else its next save checks those bytes (CRC-32)
  under the ``flock``: one failed stat never locks every save out, and another
  writer's content is still refused. A store that never loaded writes
  the whole state its caller gives it (a new chat, a migration) and is guarded
  from then on. The check holds the old file's ``flock``, so an interleaved
  append waits and is then refused. On Windows every reader of the file opens
  it sharing delete (``session_journal.open_shared``), and a replace renames
  with POSIX semantics (``win32_replace``: ``FileRenameInfoEx``, Windows 10 1607
  or later on NTFS), so it lands while a reader (a worker chat's mirror) holds
  the file, and the reader keeps the file it opened. The rename's name length
  and buffer are the target's UTF-16 encoding (code units, never Python
  characters), so a folder or file name with an emoji lands at its own name.
  Reason: sized by characters, one emoji cut ``.json`` to ``.jso``. Where that is unsupported
  (an older Windows, FAT, some network shares) the replace is ``os.replace``,
  logged once, and ``MoveFileExW`` refuses a name any handle has open, a
  delete-sharing one included: a reader's handle then defers a compaction (the
  save appends). Either way no writer holds a handle on its target across the
  replace: Windows has no ``flock``, so the guard's check closes its own handle
  and checks the file's identity again by ``stat`` just before the replace. The
  only opener without delete sharing is a writer's own append, which its lock
  serializes with its rewrites. Every identity the
  journal compares is a descriptor's (``session_journal.stat_of``): on Windows
  a path's stat can lag the file it names, and a lag read as another writer's
  change made saves rewrite (and, with the replace refused, fail at random:
  the 1.0.6 rehearsal's ``script_document_replaced`` repeats). Pinned by
  ``test_native_journal_guard.py``, which models that rule, and checked on a
  real Windows host by ``tests/python/angelo_agent/windows_journal_replace_check.py``.
* **A worker chat's file has a single owner** (``worker_journal_owner``,
  design A′). The worker Blender writes it through an ``OwnedSessionStore``,
  guarded as every store is; its load is the claim, so a file that changed
  while it was read is not hosted (``JournalTaken``), and ``release`` stops
  its writes (``JournalReleased``). Angelo marks the chat hosted
  (``hand_off``, ``sessions.HOSTED``) before ``chat.open``. While it is hosted,
  no store in Angelo writes the file (``JournalHosted``). A store loaded before
  the latest hand-off never writes it again until it loads again, so a mirror's
  stale memory can never overwrite the worker's records.
  Ownership returns when the worker answers ``chat.close``
  (``close_answered``) or once its process has ended (``worker_ended``).
  After a crash, Angelo reopens the chat exactly as it reopens one after its
  own crash: a load, then one save. This leaves the same file, because every
  admitted call's record already holds its fail-closed ``interrupted`` result
  (``journal_tool``). ``Recovered`` lists those calls, plus the calls the mirror
  showed without a record (never admitted, so never run). If Angelo ends, the
  worker stops each hosted turn, closes the chat (its compacting save) and
  releases the store (``lead_gone``). Pinned by
  ``test_native_worker_journal_owner.py``.
* **Other builds.** A file without records (an older build's, or a compacted
  one) loads as a single document, whatever its formatting. A build older than
  1.0 that opens a file with records shows an unverified, empty history
  (mutation IDs closed, so nothing is repeated) and its next save writes that
  over the file: the chat is lost, for this build too. A chat this
  build closed (chat close, a switch, quitting) is compacted and opens there
  normally; one left by a crash or a forced quit holds records until this
  build opens it again (the first save after a load rewrites it).
* **What a build cannot read, it keeps** (from 1.0 on). A chat that loads
  unverified (an unreadable file, a record or version this build does not know,
  rows or a ledger that fail validation) or with a top-level field this build
  does not know is first kept beside it, unchanged, as
  ``<id>.json.<UTC time>.unreadable`` (a hard link, else a synced copy, then the
  folder synced), and the chat says so (``SessionStore.load``,
  ``session_kept.preserve``); a later build may read it. The kept name is
  created exclusively, so a second instance keeping the same chat in the same
  second takes the next name (``-2``, ...) and never replaces another's copy; a
  copy that fails leaves neither its temporary file nor its name; a load
  removes an empty kept name a kill left mid-copy once it is a minute old
  (``session_journal.discard_stale``). Nothing removes a kept copy. Only the
  top level is checked: a later build's field
  nested inside one this build cleans (an option, a task-memory or
  runtime-session field) is dropped by the first save here, silently and
  without a copy, so a later build must add its data at the top level of the
  document or of a record, or raise the version. If it cannot be kept,
  the store refuses every save over it and the chat says so; closing it,
  switching away and quitting still work (``save_observed_state``: a save that
  fails there never stops them). Compacting at every turn's end would let a
  build older than 1.0 read more chats, but would add a whole-file write to
  every turn. Tools read transcripts with ``sessions.read_transcript``.
* **The same for the other state files** (release audit of 1.0.4/1.0.5). The
  chat list (``conversations.json``), the job records (``jobs.json``), the saved
  connections (``connections.json``), the pending team usage
  (``team-usage.json``) and the local apps' conversations (``cli/clients.json``)
  used to read a file they could not read as empty and replace it at their next
  write. Each now reads through ``kept_state.read``, which tells no file, a file
  that can't be read now (a permission or I/O error) and a damaged one (not
  JSON, the wrong shape, another version's) apart. Neither of the last two is
  ever replaced as if empty: the team usage and the local apps refuse to write
  while they can't read it (they raise, so their caller keeps the change and
  tries again; the local apps' save also merges only its own change over a
  fresh read); the job records, the connections and the chat list keep it
  beside it first, the same way as a chat (``keep_before_write``), and read that
  copy back at their next load (``kept_state.kept_values``), removing it once a
  save holds all of it. Job IDs given while the file can't be read start at a
  random point far above the usual ones, so the records read back never share
  one, and trimming never drops a record that still says where a render's
  frames are. A chat list that can't be read also lists every chat in
  ``sessions/`` (``session_kept.recovered_index``; a title comes back when the
  chat is opened or the list reads again), and a chat whose file can't be
  stated stays listed: only one known to be gone is dropped.
* **What stays on disk.** Data removed or replaced in memory (an evicted
  result, an earlier version of a row or of the task memory) stays in earlier
  records, inside the profile's private folder, until the next rewrite.

Host tests (``run_host_tests.py``) and native suites (``run_native_suites.py``,
except LIVE ones) set ``ANGELO_JOURNAL_VERIFY=1``: every save then reads its
file back and must reproduce what a whole-file save would have written, so a
row changed in place that no save was told about fails the test that changed
it. ``test_native_session_journal.py`` pins appends, torn records at every
cut, corruption, other writers, links, failed writes, compaction, the scrub,
the ledger and a randomized session; ``test_native_session_journal_cost.py``
that a save's work does not depend on the chat's length;
``test_native_journal_secrets.py`` the redactions. Further reproductions and
pins of the journal's edge cases are ``test_native_session_journal_review.py``,
``test_native_session_journal_pins.py``,
``test_native_session_journal_review2.py``,
``test_native_session_journal_pins2.py``,
``test_native_session_journal_fixes2.py``,
``test_native_session_journal_review3.py`` and
``test_native_session_journal_fixes3.py``. The manual tools (a race stress,
kill loops, full and coarse-timestamp disk images, an earlier build, the pauses'
cost, a long chat's open and save costs) are in
``tests/python/angelo_agent/journal_review``.

Reviewed Python that raises
---------------------------

A reviewed script is not transactional (``agent_execution_feedback.rst``), so
a script that raises could leave whatever it changed in the scene. The executor
answers a raise with three things (``script_errors.py``), the same for both
runtimes.

**Where it failed.** The result's ``error`` has the exception's type, its
message and the failing line ("KeyError: 'Scale' (line 143)"). ``line`` and
``source`` are the innermost line of the script itself, and ``traceback``
lists the script's own frames, outermost first, at most eight. Frames of
Blender, of Python's library and of the host are left out: they are not the
script's to fix, and their paths are not the model's to read. A ``result``
that is not JSON data does not fail a script that ran to its end: the
result is null and ``result_error`` says why. A ``result`` over 12,000
characters of JSON keeps its head as text (``result.head``) with the number of
characters it had and how many were left out, instead of being replaced by a
bare ``truncated`` flag. Printed output keeps its first 12,000 characters, and
``output_omitted_characters`` says how many were cut.
``sys.exit()``, ``exit()``, ``quit()``, ``raise SystemExit`` and
``KeyboardInterrupt`` are raises of the script like any other
(``script_errors.SCRIPT_FAILURES``): they get the same result and rollback,
and never end Angelo or leave a half-applied script without a result. The
controller's tool dispatch catches the same set for every tool.

**Names Blender renamed or removed.** Blender's own error for a name that
Blender 4.x or 5.x renamed or removed names the 5.2 replacement
(``bpy_angelo_renamed_api.cc``, a table consulted only after a lookup has
failed). ``sky_type = 'NISHITA'`` raises stock's ``TypeError`` with the valid
items, followed by "Blender 5.0 renamed 'NISHITA' to 'SINGLE_SCATTERING' (the
same model: files saved with 'NISHITA' open with it); the new default is
'MULTIPLE_SCATTERING'". The table lists the four names of 36 of 93 recorded
agent scripts that raised (both Angelo and Blender + MCP, three models):
``'NISHITA'``, ``'BLENDER_EEVEE_NEXT'``, a Sky Texture's ``dust_density`` and
an Action's ``fcurves``. It also lists exact renames with no recorded use:
an Action's ``groups`` (removed with ``fcurves``) and eight Principled BSDF
inputs that 4.0 renamed (``inputs['Specular']`` and the like). Only the text
changes: there are no aliases, so a listed name still fails as in stock
Blender 5.2, with the same exception type at the same point, and a script that
runs in Angelo also runs in stock Blender. An ``AttributeError`` for a listed
name keeps its ``name`` but has ``obj`` None, so Python appends no
closest-spelling guess, which for ``dust_density`` was the unrelated
``air_density``. A name that is not listed, or a listed one used elsewhere
(``'NISHITA'`` as a render engine, ``fcurves`` on an Object), gets stock's
error. The table applies to every script and add-on, not only the agent's;
``test_blender_renamed_api_errors.py`` checks each row against the build.

**A script that replaces the document.** A script can load, revert or recover
a file, or read the startup file, under any operator name. That frees Main,
and in a background Blender (the headless child, a parallel worker) also the
window manager and the window the executor holds; reading the scene back
through that window crashed the process up to 1.0.5. While a script runs, the
host watches Blender's ``load_post`` (a load that succeeded; a failed one fires
``load_post_fail`` and replaces nothing) and the window manager; a script that
removes the handler counts as having loaded (``script_document.py``). Host
hooks inside the script that hold more than IDs (the parent-inverse hook's
view layer) stop once the document is gone. After the script, whether it
returned or raised, the result is ``DOCUMENT_REPLACED``: nothing held from
before is read back, no rollback is tried, the apply operator pushes no Undo
step, and what the script changed is unverified. It is a host decision, not a
script error: the request ends so the person looks first. The headless child
then loads the session's own file again and saves nothing of the run. A
parallel worker's script (approved in Angelo, run in the worker only when its
invocation digest is the approved one) never makes another file its part
(``worker_script.py``): one whose code names one of these operators, as an
attribute, a name or a word of a string, is refused before its card and again
in the worker, with nothing run (appending or linking from a file is not a
load); after a load the check could not see, the worker loads its part again
as last saved and saves nothing, or ends if it cannot; after a script that
saved the open file as another file, that file stays and the worker saves the
same data as its part again. Undo or
Redo inside a script is not a replacement; the next paragraph covers those
(``test_blender_script_document_replaced``, ``test_blender_headless_script_document``).

**No held window or scene is read after it is freed.** A load, a closed window
or a deleted scene frees what a held ``bpy.types.Window``, ``Scene`` or
``ViewLayer`` points at, and Python is not told; macOS read the freed memory and
went on, Windows faulted (NTSTATUS 0xC0000005, the 1.0.6 C2 build: the
executor's window read after a script replaced the document, and after a load).
So every holder keeps an identity, never the object, and each read finds the
live one in Blender's own lists, or None once it is gone (``live_target``). A
window's identity is its address, its window manager's ``session_uid`` (a
load that replaces the window manager, and so its windows, gives the new one a
new number; a failed load or one that keeps it keeps its windows) and a
lifetime token from a survey of the window list at each bind and read (main
thread only; a worker's lane never reads local windows): a new address, or
another screen on the same workspace that is not the window's own maximized
area or the way back from it (Blender names that screen "<screen>-nonnormal"),
is another window. Residue for 1.1 (a native per-window id): between two
surveys, however far apart, a closed window replaced at its address on another
workspace, or showing a screen at the closed screen's address or named as its
maximized area, is not seen; the binding then reads that live window. A scene's is its ``session_uid`` (one per ID per
process), a view layer's its scene's and its address among that scene's
layers. Held this way: the executor (a script it runs may load), the program
backend (read again at each poll's ``check_context``), a job and its setting
overrides (a look's restore ends cleanly with its scene deleted, and its render
slot always comes back), the team library's jobs and download completions, the
script kit's scene, a script's parent-inverse view layer, and every deferred
callback and generator: the Media Studio / Generation Flows tab build (it
reads the window after every yield, holds workspaces by name, and stops when
the window is gone), merge framing's retries and settle, the asset shelf's retries, the
setup and start cards' answers, a message delivered on a worker's lane or after
its relaunch, a finished render's picture, and the Media Studio importers kept
on a chat. ``test_native_live_target`` poisons held objects, covers address
reuse and loads, and scans attributes of any name, dictionary entries and
closures (each exception named with its reason);
``test_blender_parallel_scene_switch`` S14 and S15 free real ones.

**What state the scene is in.** Before a reviewed script starts, the host
makes an Undo restore point with a one-time marker in Main (Blender's data
blocks). It does this only in Object, Pose, Weight Paint and Vertex Paint
modes, whose Undo steps hold Main data. Edit, Sculpt and Texture Paint modes
use other Undo stacks, so no whole-file restore point is claimed there.

When a script raises, the host records its partial state and steps back at
most 16 times to find that marker. The original mode and object read-back
must also match. ``main_data_restored: true`` proves that the restore point
was loaded; it is not a claim about image pixel buffers or external effects.
Two things can bring the marker back with the script's changes, and both drop
the claim (``script_watch.py``). A script that undoes, redoes or loads a file
can replay the restore point and push its own step over it:
``HistoryWatch`` notices Undo, Redo and load handlers while the script runs
(a handler the script removed counts too), as does the marker left in the
live scene, and the host then undoes nothing. Undo also does not reload linked
library data, neither the properties a linked ID lets you edit (a linked
object's modifier visibility) nor editable assets such as the paint brushes
Blender links from its asset library: ``linked_state`` digests exactly those
values, per linked ID, at the restore point and after the reload, and a
difference puts back what the script left. A brush linked during the script
(entering a paint mode links one, and Undo keeps it) is admitted only while
Blender reports it unchanged since it was read (``has_unsaved_changes``); any
other new linked ID is a difference. Asset preview thumbnails and other
dynamic-length arrays are not read. The digest costs nothing without linked
data and about 3 ms per digest for a paint mode's brush (two per script);
200 linked objects took about 20 ms. Neither proves the absence of an effect:
arbitrary Python can still reach the host's own frames.
The operator cancels its own Undo push after this reload, leaving the restore
point as one harmless step. When a finished script leaves Undo history and
preferences intact, the outer operator replaces its restore point with a
grouped step, so two such scripts remain two steps. The host does not verify
that condition for arbitrary Python; the success receipt reports coverage as
unverified, as described below.

Recovery always reports ``rollback_scope: "MAIN_DATA"``, ``rolled_back:
false`` and ``partial_changes_possible: true``. It omits ``scene_changed``:
restoring Main cannot prove that the whole scene is unchanged.
``rollback_scope_excludes`` names the interface data memfile Undo never
reloads (``window_manager``, ``workspaces``, ``screens``, their custom
properties included), and the ``rollback`` text says so. Image buffers,
files, preferences, the window layout and other external effects are not
verified or restored, recorded as ``image_pixels_restored: false`` and
``external_effects_restored: false``. The object change list does not cover
all scene settings or images. A source-name scan and image dirty flags or
packed sizes can identify some pixel writes, in which case automatic Undo is
not attempted; unchanged flags or equal packed sizes never prove unchanged
pixels. No pixel buffers are copied or loaded to provide these facts.

If no restore point is available or reached, Main data is not claimed
restored. Handles become stale after a Main reload, staged previews are
discarded and pending calls are cancelled by the normal Undo lifecycle
(``invalidate_workspace_state``). Scene images are invalidated before every
script, including partial recovery. The model must inspect unverified effects
before writing a correction, with the pre-request snapshot available through
the normal recovery flow.

A finished script reports ``undoable: false`` and ``main_data_undoable:
false`` with ``undo_scope: "MAIN_DATA"`` because its Undo history remains
unverified. Arbitrary Python can alter that history through direct or computed
calls; the original restore point and an available Undo operator do not prove
one-step restoration. ``undo_available`` is a separate snapshot at script
completion: Global Undo is enabled, its step limit is positive, and the Undo
operator's poll succeeds. It promises neither future availability nor coverage
of this script; the outer operator publishes its grouped step afterwards.
Every finished script also reports ``image_pixels_undoable: false`` and
``external_effects_undoable: false``. Execution, preferences and actual Undo
behavior are unchanged; these facts require no pixel copies or history reads.

**What it changed, by handle** (``script_after.attach``). A completed script's
``after`` lists the scene's settings entry (handle and settings revision,
never left out: ``revision_budget``) and one row per object it added or
modified (at most 24): handle, world-space ``bounds`` to 1 mm when it has
geometry (``object_bounds``: the evaluated bounding box through its matrix)
and exact revision, so a program can continue without a lookup, and an added
object's size is in the result itself rather than only its vertex count.

**Where what moves is, at a few frames** (``motion_facts``, ``motion_frames``,
``motion_scope``). After a script or a committed program that adds or changes
an object that animates, the result carries ``motion``. What animates is
decided exactly on animation, not by type (``motion_scope.Scope``): keys,
drivers or NLA on the object, its data or its shape keys; a modifier that
reads time (Wave, Build, caches, physics, emitter particles or hair dynamics);
a Geometry Nodes group with a time, simulation, bake, image or camera node; a
constraint, modifier, curve or node socket naming an object that animates
(every Object and Collection pointer, from Blender's own property list); a
collection instance of animated objects, particle instances (any Object or
Collection the particle settings name) and a vertex or face instancer's
animated children; a parent that moves. A static curve,
text, Boolean or Displace is not animated by its type, so a still wall's edit
reads nothing and a keyed car on a still road curve is its own group. Each
question visits at most 256 objects; past that it counts as animated, and a
parent whose motion is unknown ends the climb to the top.

* ``frames``: the scene's first, middle and last frame and the edited objects'
  own key frames between them, at most 5 (keys chosen evenly);
* ``moving``: for each hierarchy holding an edited object that moves (its top
  is the highest object of the chain that moves: a car under a still scene
  root or road is the car, a mesh on an animated rig's bone is the rig), at
  most 6: its handle and name, its parts (names when at most 8, else a count),
  the top object's world origin and the world box of the whole hierarchy's
  evaluated rendering geometry at each frame (``object_bounds``; the instances
  its parts make included, from Blender's instance list, each with its own
  object's box (a curve's without its point radius), at most 10,000, past
  which ``instances`` says so and the box is null rather than a box of some of
  them; an instancer's own base mesh only when it renders), so an origin that is right
  with geometry that is not shows as two different numbers. Parts render (a
  rendering collection of the view layer, not hidden for render) and are no
  boolean operand; rendering parts the view layer does not evaluate are named
  (``parts_not_evaluated``); a hierarchy past 4,096 objects says so. Groups are
  chosen by the edit's changes, never by names: the objects its change list
  names, then those Blender's own depsgraph updates show the edit changed (a
  transform or geometry update, or an object whose Action the update lists;
  an update at another frame than the last one seen is time moving, not an
  edit), which the change list's caps can leave out (1,000 objects, 64 curves
  of an object). A group moves when its origin
  or box changes by more than 0.1% of its size (a millimetre model reads like a
  bridge; origins keep the box's decimals);
* ``apart``: within a moving group, parts whose own motion (keys, drivers, a
  constraint) takes their first-frame geometry centre more than 5% of their
  size from where the bone, object, vertices or path they are parented to
  carries it: the part, its parent, the frame and the offset, at most 8. The
  centre moves rigidly with each matrix, so a rig's deformation of its skin is
  no offset and a wheel spinning on its own hub barely is. A fact, never a
  refusal or an extra round.

A script's change rows include ``motion`` among an object's fields when the
keys, drivers or NLA of the object, its data or its shape keys, its
constraints or its parent changed (``motion_signature``; an object's keys read
whole up to 1,024, then 16 evenly spaced keys of each longer curve, so one
object's digest never depends on another's), so an edit that only keys an
object or a shape key is followed. ``angelo_scene_object`` takes
``frames`` (at most 8) and returns ``at_frames``: the object's origin and box,
and with rendering children the hierarchy's box, at each; an object the view
layer does not evaluate is not measured.

The values are Blender's own evaluation (``motion_frames.Sampler``): each
frame is set with ``scene.frame_set``, the update every view layer's
depsgraph runs for a new frame, with no ID tagged as edited, so no baked cache
is outdated. Unlike setting ``frame_current`` it queues no frame-change
notifier, whose interface update would replace every unkeyed edit of an
animated channel in the scene after the read; publishes no message-bus change;
and does not clamp a negative frame. It evaluates every view layer of the
scene, and an active depsgraph writes its values back, so the read covers
every layer: the edit's own pending updates are evaluated first, then the
stored value of every channel any layer's depsgraph animates is kept
(``animated_values``); afterwards the frame, subframe, stored values (an
unkeyed edit outlives the read, in every layer) and scene camera are put back,
every layer's depsgraph is updated, and
``frame_not_restored`` says when the frame did not come back. Selection is
never touched; the change feed, view cache and scene revision counter ignore
the read's own updates, so a frames read never stales an approved script.

Not measured, with the reason (``motion_guards``): playback, a render, a
bake, an export or an Angelo job running; an object in a mode other than
Object or Pose; a view
layer no depsgraph has evaluated (a frame change would build one for each: six
never-shown layers held 2.7 GB after one read); scripted drivers or
frame-change handlers that do not ship (``render_extensions``), and for a read
the model asks for in the Read-only or Ask mode depsgraph-update handlers,
message-bus subscribers to a value it puts back and a Blender interaction in
progress, as for a view strip; a simulation a frame
change would step (unbaked cloth, soft body, dynamic paint, emitter particles
or hair dynamics, a fluid domain without baked data, an unbaked rigid body
world, in any view layer, or a Geometry Nodes simulation zone not baked to
disk, since a packed bake's state is not readable from Python; static hair, an
Ocean and a Bake node outside a simulation do not step); more animated
channels than a read keeps; fewer than two
frames read. ``MAX_SECONDS`` is 0.3 s of wall time, the stall a person sees
(evaluation runs on many threads, so the main thread's CPU time understated it
several times), counted from the read's first scene-wide check (the edit's own
pending update before it is the edit's): before each frame the time spent plus
twice the slowest frame so far must fit, a frame's cost being its evaluation
and its measuring together; the first and last frames are read first, and
frames not reached are listed (``not_read``). A scene whose read projected
more than the budget for two frames (its work beside the frames plus two
frames) is not measured for 60 s, twice as long each time it is slow again, up
to an hour, checked before the scans that grow with the scene, so a heavy scene
stalls rarely for no fact. Measured (Apple M4
Pro, background):
a Geometry Nodes grid of about 2 million vertices moved by time takes 212 ms a
frame; its read stopped after the first frame and the return in 398 ms and
said so, and the next read was refused in 0.3 ms; 2,000 parts under 200 keyed
rigs 170 ms. A frames read costs the schema 101 B of plan text and 291 B of
Codex dynamic tools.

An animated object built in the wrong frame of reference (a parent inverse
from a stale matrix, local vertices written as world ones, children never
parented) keeps a correct animated origin with misplaced geometry, so a check
of the origin alone passes; the per-frame box shows the difference. Replayed
on recorded final scenes with such a vehicle, the read states each case (the
origin and the box apart, a box of the wrong width, parts missing from the
moving group); on passing scenes origin and box agree with the grader. Cost
on those scenes: 45-83 ms; 316-413 B, about 60 B per group and frame plus part
names.

**Names Blender gave in place of the names asked for** (``script_renames``).
Blender keeps a data-block's name unique among the local data-blocks of its
kind: a script that creates "Camera" beside the factory camera gets
"Camera.001", ``obj.name = "Gate"`` while another "Gate" exists gives
"Gate.001" (or leaves a "Gate.001" as it was), and a copy, a primitive added
again or a mesh made under the name of one left behind is numbered the same
way. A completed script's result carries ``renamed_by_blender`` when that
happened to any data-block the script created or named: per ``bpy.data``
collection, the names Blender chose (``{"objects": ["Camera.001"], "cameras":
["Camera.001"]}``). The kinds models look up by name come first and the kinds
an Object's data is (meshes, cameras, lights and the like), which models reach
through their objects, after them; a run of numbers of one base is one entry
(``"Cube.001 to Cube.058 (58)"``); at most 12 entries, the rest counted per
kind (``omitted``). Where the name asked for is known, it decides: while the
script runs, and only then (``script_rename_watch``), every
``<data-block>.name = ...`` is observed (``bpy.types.ID.__setattr__``), so an
assignment Blender answered with another name (taken, or cut to 255 bytes) is
listed even when the name did not change, and a numbered name the script
assigned and got is its own; a library override asks for its reference's name;
and what a file-reading call brought in (an operator with a file path:
imports, ``wm.append``, ``image.open``; and ``bpy.data.libraries.load``) is
named after the file and never judged by its name's form. Anything else the
script created or renamed follows Blender 5.2's rule (``main_namemap.cc``),
checked against the compiled app: a name of the form ``<base>.001`` (three
digits to ``.999``, then ``.1000`` and up) is listed when another local
data-block of its kind holds the base, after the script or before it; names
are at most 255 bytes, and where ``.001`` no longer fits (a taken name of 252
bytes or more) Blender cuts one character instead, so a name one character
shorter than a held name of 252 bytes or more is listed. Never listed:
data-blocks the script neither created nor named (an earlier clash is not
repeated), names whose base nobody else holds ("Post.001" with no "Post"),
linked data-blocks, the Render Result and Viewer images, and anything after a
script that loaded another file (none of the scenes and objects it found is
left; Undo keeps session UIDs). Not told apart outside an assignment:
``bpy.data.<kind>.new("Post.001")`` while "Post.001" is taken and "Post" free
(not listed) and ``.new("Post.001")`` beside a "Post" (listed); a name Blender
numbered or cut inside an import or append (not listed); none occurred in
1,279 distinct recorded scripts, none of which imported, appended or overrode
anything. Every kind models look up by name is read; object data only while
the whole stays within 50,000 data-blocks, each kind counted before its names
are read, and a kind past that is named in ``not_checked``. A failed or
rolled-back script gets nothing; a reading or an observer that fails leaves
the field out. Typed programs state their own names (``stage_names``); this
field gives a script's result the same fact. Replayed, 44
of 793 recorded scripts got the field, 24 of them through an observed
assignment (costs and bytes: performance.rst, "Names Blender gave a script's
data-blocks"). ``test_native_script_renames.py`` covers the rules and the
observers on hand-built readings and stand-ins;
``test_blender_script_renames.py`` checks them against Blender 5.2's own
naming through the executor.

**What its Booleans did** (``script_booleans``). A script that adds a Boolean
modifier and applies it can take the whole block or nothing when a cutter is
wound inward, turned the wrong way or overlapping, and a face count printed by
the script does not show that the opening never formed. A completed script's
``booleans`` states, as measurements, what each Boolean did:

* **Applied** (``script_booleans.Applied``): while the script runs, and only
  then, ``bpy.ops`` builds ``object.modifier_apply`` as an observed callable
  (it replaces the module global ``bpy.ops._op_create_function``, through which
  ``bpy.ops`` makes every operator callable, and puts it back however the
  script ends; a ``temp_override`` and an ``EXEC_DEFAULT`` positional are
  observed too). The objects are those Blender's exec applies to
  (``use_selected_objects``: the selected editable objects, else the context's
  ``object``, else the active object), and the Booleans the call may apply are
  the one named by ``modifier`` or, without it, every Boolean: Blender's exec
  applies none then, and the row says ``"applied": false``. Applying one
  modifier acts on the base mesh alone, even one that is not first, so the
  base mesh is measured before and after the call, in the world matrix
  Blender evaluates for it (as the operator does).
* **Live** (``script_booleans.live``, from ``script_after.attach``): each mesh
  object the script added or modified, or whose Boolean operand (an object,
  or an object in an operand collection) it added or modified, whose enabled
  modifiers are all Booleans: its base mesh against its evaluated mesh. An
  object in Edit Mode, with shape keys or with a deforming parent is skipped.

A row (at most 8, applied first; ``booleans_more`` counts the rest)::

    {"object": "Gatehouse", "with": "Arch Cutter", "operation": "DIFFERENCE",
     "solver": "EXACT", "applied": true, "faces": [6, 10],
     "volume_m3": [12.0, 12.0], "fact": "volume unchanged (within 0.0012 m³)"}

``with_collection`` names a collection operand; a live stack of several
Booleans names up to four in ``stack``. ``volume_m3`` is the world volume
where the mesh is closed (every edge has two faces) and wound one way, else
``volume`` says why (``open mesh``, ``faces wound both ways``, ``no faces``,
``over 250000 triangles``). ``bounds`` (world, 1 mm) appear only when they
changed. A ``fact`` appears only at the extremes: no faces left; the volume
unchanged (and the faces, when they are) within a bound it states in m³; the
share kept below half; a difference or intersection that grew, or a union
that shrank, by more than 0.01% of its volume, which no Boolean of closed
solids does; a negative volume (faces wound inward). The bound is 0.01% of
the smaller of the object's and its operands' closed volume, as Blender
evaluates them: a Boolean changes an object by at most its operand's volume,
so a 4 cm bolt hole through a 40 m³ wall (0.00126 m³, under 0.01% of the wall)
does not read "volume unchanged" like a cutter wound inward. Without the
operands' volume (open, unevaluated, past the triangle
cap, or a collection of more than 16 meshes) only a mesh whose face count did
not change is judged, against its own volume. An object the view layer does
not evaluate (disabled in viewports, in an excluded or viewport-disabled
collection) has no evaluated mesh, and its matrix may be stale (an apply cuts
in it): in its live row, and in place of its applied row's world measures,
``volume`` reads "not evaluated: disabled in viewports or excluded" (an
applied row keeps its face counts), with no fact: measured, such an object
would read "volume and faces unchanged" while a render cuts half of it. The
volume is the signed sum over each face's fan of triangles, about the
vertices' mean, times the absolute determinant of the world matrix: its sign
is the winding's, since Blender's Boolean flips an operand whose transform is
negative (``mesh_boolean.cc``), so a mirrored object is not inside out. Closed
parts that overlap count twice. Under the EXACT solver a cutter wound inward
left a 64 m³ block's volume unchanged while its faces went from 6 to 10; with
the MANIFOLD solver the same cut grew it by 25%.

Optional evidence: an error while measuring counts or drops a row, never
fails a script, and a failed or rolled-back script gets nothing. Each reading
spends at most 0.1 s of its thread's CPU on measuring, operands included (a
row past it is counted), reads at most 250,000 triangles per mesh and searches the first
5,000 scene objects for a changed operand. Limits: ``object.convert``
(none of 1,021 recorded agent session files that apply a modifier and name
a Boolean calls it), meshes copied from an evaluated object (``new_from_object``,
``to_mesh``; ``kit.cut`` proves its own), Edit Mode's
``mesh.intersect_boolean`` and ``bmesh`` operations, an apply another
operator makes in C, a target whose cutter moved without the target or the
cutter being in the change list (only the first 1,000 objects are), and a
live stack with any other enabled modifier. ``test_native_script_booleans.py``
pins the measure, the facts, the rows, the hook and the live reading with
stand-ins; ``test_blender_script_booleans.py`` (BACKGROUND) runs every case
through the real executor, with volumes checked against Blender's own
``bmesh`` volume on held-out shapes. Cost: ``performance.rst``, "Boolean
results".

**What the World shows, and renamed names** (``script_after.notes``). A
script that changed the World, completed or raised, reports ``world``: what
the scene's World shows a camera (``world_surface.shows``), read with the
benchmark grader's rules: the World Output the renderer evaluates for the
scene's engine, the nodes its Surface reaches through links the renderer
follows, then the Sky Texture's type and sun elevation (``SKY``), or the flat
Background colour and its colour family (``COLOR``), or the nodes that drive a
colour the host does not evaluate (``NODES``), or ``NOTHING``. A cheap digest
of the graph's structure and input values before and after the script decides
whether it changed (``world_surface.fingerprint``). Every finished script's
result also carries ``api_notes`` when its code uses a Blender 4.x name that
5.2 renamed or removed (``script_api_notes.RENAMED``: ``'NISHITA'``,
``'BLENDER_EEVEE_NEXT'``, ``dust_density`` and an Action's ``fcurves``),
inside try/except included: there the old name fails silently and the
fallback runs, while the model may still believe the old name took effect
(a physical sky set inside try/except leaves a flat fallback colour). Both
runtimes' instructions state the 5.2 names beforehand, in one shared sentence
(``agent_guidance.BLENDER_API_FACTS``, ``agent_integration.rst``); the notes
catch a script that uses an old name anyway. Of
1,094 distinct recorded Angelo scripts, 240 used ``'NISHITA'``, 264
``'BLENDER_EEVEE_NEXT'``, 153 an Action's ``fcurves`` and 37
``dust_density``; 9 raised and the rest failed silently or were guarded.
Against the grader on 140 recorded final scenes of ten scenario groups, the
host's reading agreed on all 81 skies (type and elevation) and all 55 flat
colour families; 4 node-driven worlds are named, not evaluated. Cost, measured
in stock Blender 5.2 at ``nice 19``: a World fingerprint takes about 10 µs for
the default World and 0.5 ms for 42 nodes, twice per script; the notes parse
only a script whose text has a listed name, about 3 ms for 6.6 KB. A typed
``world_configure`` and a World ``node_edit`` report the same ``shows`` for the
staged World. ``test_native_world_surface.py`` and
``test_native_script_api_notes.py`` cover the rules without Blender;
``test_blender_script_api_notes.py`` checks every listed name against Blender
5.2 and reads real Worlds.

**Where the sun is, and how bright the World shows** (``world_light.py``).
Without these facts a model has to guess where ``sun_rotation`` points, sets
the sky's sun by estimate apart from the Sun light, cannot confirm that haze
settings took, and cannot tell how bright a sky renders. ``shows`` therefore
states facts, no verdicts:

* ``sky`` carries the sun as ``sun_bearing_deg`` beside ``sun_elevation_deg``
  and every setting as it is now (sun size in degrees, disc, intensity,
  altitude and densities; turbidity and ground albedo for the analytic types),
  so a value a script's try/except failed to set shows as it is. One
  convention for every direction, stated in the result as ``directions``:
  toward the sun, the bearing in degrees clockwise from +Y seen from above (+X
  is 90) and the elevation above the horizon. For the scattering skies
  ``sun_rotation`` is that bearing (Cycles and EEVEE put the sun along (cos e
  sin r, cos e cos r, sin e)), and an elevation past 90 degrees folds over the
  zenith; the analytic skies read ``sun_direction``. The node's own texture
  mapping or a linked Vector input turns the sky away from this reading, which
  ``sun_turned`` says. Every typed result's ``shows`` has these.
* With the live scene (a script's ``world``, a committed program's ``world``
  and the ``angelo_scene_settings`` read's ``world_shows``): ``sun_lights``, up to four Sun lights that render,
  each with its bearing and elevation (its +Z, where its light comes from),
  strength and ``from_sky_deg``, the angle from the sky's sun; and
  ``brightness``: ``samples`` of the World's scene-linear colour (the flat
  colour times its strength; for a Sky the Background shows directly, the
  measured default sky, ``world_light_table``, times its strength, toward the
  sun, away from it and at the zenith) with ``shown``, what the scene's view
  transform, look, exposure, gamma and curves make of each on screen (0 to 1 a
  channel). ``shown`` runs Blender's own display transform: the values are
  written to a one-row Radiance HDR, saved through ``view_save``'s native
  ``save_copy`` with ``display`` as an uncompressed BMP, and read back; no
  data-block is added and no setting is written. Another atmosphere than the
  measured default, a sun more than 6 degrees below the horizon, or a sky
  reached through other nodes is not read (``unread`` says why, or there is no
  ``brightness``). The fingerprint also covers the sky's settings, the Sun
  lights and the view transform, so a script that only turns the sun is
  reported.
* A committed program compares the fingerprint before its writes with the
  live one after (``program_commit_report._world_before``; Sun lights by world
  matrix, so a Sun moved through its parent or a constraint counts), and when
  they differ its result carries the same ``world`` as a script's; a Claude
  context's compact entry keeps it (``result_facts.commit_facts``). It is
  optional evidence read after the live writes: any failure to read it (a full
  temporary folder for the brightness reading included) leaves it out and
  never fails the commit.
* A Sun light counts (here and in ``rendered.lighting``) only when it renders:
  its collection chain is neither excluded nor hidden for render in a used view
  layer and it is not hidden itself (``world_light.rendering``). With the
  scene's Sun lights beside the sky, ``from_sky_deg`` shows a sky sun set from
  an azimuth measured from +X (``sun_rotation`` is a bearing from +Y) as far
  from the Sun light it was meant to match. Both runtimes' instructions state
  that frame beforehand, and how the two scattering types differ in colour
  (``agent_guidance.BLENDER_API_FACTS``, ``agent_integration.rst``).

``world_light_table.SKY_TABLE`` is Blender 5.2's own sky, measured once
(``measure_sky_table.py``: Cycles, the World alone, a square 50 mm view per
direction) and read by linear interpolation between its sun elevations.
``test_native_world_light.py`` pins the convention, the lights, the samples
and the encoders without Blender; ``test_blender_world_light.py`` checks in
the compiled app that a narrow camera aimed at the stated bearing sees the
sun's disc, that ``shown`` equals ``Image.save_render`` in Standard, AgX and
Filmic with exposure and a look, and records the table's interpolation error
at the zenith against a render.

**What happens next.** The model reads the result in the same turn. A Codex
thread gets it as the tool's result. A Claude plan stops at the script, and
the next round's prompt names the error and its line, distinguishes restored
Main data from unverified effects, and says that the plan's later
operations did not run (``agent_integration.rst``, "Failed plan steps"). The
person reads a system row with the error and what became of the script's
changes. The result's ``recovery`` says what is safe next: after a restored
Main, that sending a corrected script is safe and that the same script
unchanged fails the same way (pixels and files it wrote are not restored);
otherwise, that what the script changed is still in the scene and must be
inspected first, because a correction written for the earlier scene would apply
it twice. It does not forbid a retry: with Main restored, such a sentence
makes a model abandon a working approach. The host retries nothing. A
corrected script is a new call: it is
validated, shown and approved under the chat's approval mode like any other,
through the same dispatch (``routing.RuntimeContext.request_tool``), so
whatever that mode decides for a script decides for a corrected one. It can
be sent as a patch of the script that raised (`Scripts by id`_).

**What bounds it.** One request corrects at most ``SCRIPT_CORRECTIONS`` (5)
scripts that raised. They are counted in the request context for both runtimes
(``script_errors.ScriptBudget``), apart from the 2 corrections of rejected or
refused plans, so neither count can use up the other. The script after the
fifth correction still runs; if it raises too, the person is told the
corrections are used, a Claude turn ends as interrupted, and a Codex thread is
refused any further script in that request. Around those counts a request is
bounded as usual: 16 plan rounds for Claude, each round by the limits of `How
long a turn may run`_, every script by its approval, and Stop at any time.
``test_native_script_errors.py`` and ``test_native_turn_recovery.py`` cover
the rule without Blender; ``test_blender_turn_recovery.py`` runs it in the
compiled app (a script that adds one object, deletes another and raises), and
``test_blender_script_rollback.py`` checks the restore point and pixel-buffer
limits in an isolated background process with an initialized Undo history;
``script_review_fixtures.py`` adds exits, history replays, linked data, brush
assets and interface data.

A script that refers to the application preferences (``context.preferences``,
``bpy.ops.preferences`` or the user-preference file operators;
``fallback_categories.uses_preferences``) adds a sentence to its approval card:
a change there applies to every project, and neither Undo nor a snapshot
restores it. The check is the same host-derived heuristic as the script
categories: a reference, not proof of a write, and code that hides the name is
covered only by the card's general warning.

A script's context override cannot pair a scene with another scene's view
layer. Stock Blender 5.2 evaluates such a pair, and crashes, after
``temp_override(view_layer=<another scene's layer>)`` or
``temp_override(window=w, scene=s2, view_layer=<s2's layer>)`` (the window
drops the ``scene`` given in the same call) followed by any ``bpy.ops`` call
or ``evaluated_depsgraph_get()``. In Angelo, ``temp_override`` raises
``TypeError`` on enter for a ``scene`` or ``view_layer`` it names that cannot
apply (another scene's layer, a wrongly typed value) and leaves the context as
it was, so the script gets an ordinary raise with its line
(``bpy_rna_context.cc``). A view layer inherited from an outer override that no
longer belongs to the context scene (a nested window or scene override, an
Edit-mode switch with a second window open) resolves from that scene instead
(``CTX_data_view_layer``). The window first and the scene and view layer in a
nested override still pair them. ``test_blender_context_override_pair.py``
covers both; ``--gui`` adds the second-window case. An explicit
``scene=None`` or ``view_layer=None`` is not checked and still reaches
operators that need one.

A script that passes a removed data-block to a ``bpy`` helper taking IDs gets
``ReferenceError``, as for any other access to it. Stock Blender 5.2 crashes
on ``bpy.data.user_map(subset=[obj])`` after ``bpy.data.objects.remove(obj)``:
``pyrna_id_FromPyObject`` and ``pyrna_id_CheckPyObject`` read the removed
wrapper's cleared RNA type. In Angelo they treat removed data as no ID, and
``pyrna_id_FromPyObject`` raises the usual ``ReferenceError`` that its callers
keep (``bpy_rna.cc``): ``user_map``, ``file_path_map``,
``file_path_foreach``, ``batch_remove`` and ``libraries.write`` in
``bpy.data``, a custom property set to the removed ID,
``GeometrySet.from_evaluated_object``, ``InlineShaderNodes.from_*`` and a USD
hook's ``export_texture``. ``file_path_foreach`` and ``batch_remove`` check
the whole list before visiting or deleting anything, and ``file_path_foreach``
visits a copy of it, so a callback that grows or clears the caller's list does
not crash (stock Blender crashes on growth).
``test_blender_removed_id_python_api.py`` covers every helper, with live IDs
and non-ID values unchanged.

The check only sees wrappers Blender invalidated: the one passed to
``remove()``, and an ID's current shared wrapper whenever the ID is freed
(``remove()``, ``batch_remove``, ``orphans_purge``;
``BKE_libblock_free_data_py``). Two kinds of wrapper are never invalidated
and still behave as in stock Blender, crashes included. One is kept from
before its type changed (a light's or texture's ``type``, ``type_recast()``):
the next access that wraps the ID (iterating ``bpy.data.lights`` does)
detaches it (``pyrna_struct_CreatePyObject``). The other is made by a Python
subclass constructor (``Sub(obj)``). So after ``light.type = "AREA"`` and a
loop over ``bpy.data.lights`` that removes the light, the ``light`` a script
still holds reads and writes freed memory, and ``batch_remove([light])``
crashes. Closing this needs the refined wrapper retyped in place or every
wrapper tracked; it is not done.

Scripts by id
-------------

Without a way to name an earlier script, a model loses a script's text between
calls and re-sends whole scripts for one-line fixes (a Codex code-mode cell
never keeps its text; a raised script is rebuilt from memory). A later turn's
context does not carry earlier tool inputs (``routing.handoff`` hands off
results), and re-sending a whole script costs the output tokens and the time
to write it again. ``script_store.py``:

* Every finished script's result, completed or raised, names it: ``script``
  is ``s`` and the first eight hex digits of the SHA-256 of its code
  (``script_after.notes``). The id is the text's own digest: no counter, no
  new saved state, and the same text always has the same id.
* The text comes from this conversation's own transcript: every script call's
  row keeps the arguments it was admitted with (``activity_rows``, redacted of
  the connection's key, saved with the chat), and calls still pending are read
  too, since a row is added when the main thread pumps the admission event.
  Never another conversation's rows, a collaborator's call, or anything a
  provider holds. Nothing new is stored or sent.
* ``angelo_script`` (read-only; deferred in Claude's plan and on Codex)
  returns a script's description, code and line count. Collaborators cannot
  call it: they read the scene, references and scoped files, not this
  conversation's scripts.
* ``angelo_execute_python`` takes ``base`` (an id) and ``edits``
  (``[{find, replace}]``) instead of ``code``. Each ``find`` must occur
  exactly once in the text the edits before it left (they apply in order, so a
  later edit sees an earlier one's result); no edits reruns the base unchanged.
  ``Controller.resolve_call`` builds the whole code first, before validation,
  the invocation digest, consent and the approval
  card, on both runtimes and in a Claude plan (whose resolved operations are
  what runs). The person therefore reviews, and trust or "Always allow"
  covers, exactly the text that runs, as for a script sent in full, and the
  call's transcript row keeps that text, the base of a next patch.

Refused before anything is shown or run, as a ``VALIDATION`` refusal that
changed nothing: ``base`` with ``code``, ``edits`` without ``base``, more than
32 edits, an id no script of this conversation has, an id two different texts
share, and an edit whose ``find`` occurs other than once, overlapping
occurrences counted (the refusal names the edit and the count). A patch call
delivered again is built from what its first admission built for those exact
arguments (in memory, the last 64), so it is answered as the replay or
duplicate it is even when the transcript changed in between (a key bound
later redacts the base row); that map authorizes nothing new. A row whose text held the connection's key holds
``[REDACTED]`` instead, so its digest no longer matches the id the result gave:
such a script is never a base and must be sent in full. The built code then
meets every rule of a script sent in full (32,768 characters, no NUL, valid
syntax). In a Claude plan's progress row, a patch's edits are its text, never
steps (``plan_progress``). A patch is a script like any other for the
correction budget (`Reviewed Python that raises`_).

Claude's plan carries ``base`` and ``edits`` with a one-line description on
``base`` (+351 B) and the deferred tool's name (+16 B): 54,455 to 54,822 B.
Codex and voice declarations carry the tool's patch sentence and
``angelo_script``: the voice row grows 100,835 to 101,648 B of its 102,000 B
budget. Toolset 39 keeps its number: both forms are accepted, and a Codex
thread that started earlier keeps its declarations, without the patch form,
until it starts fresh. ``test_native_script_store.py`` covers the rules, the
real Controller's admission, review and dispatch, the fetch and the Claude
plan path.

The script kit and self-checks
------------------------------

Every reviewed script finds ``kit`` in its namespace (``script_kit.py``): exact
building blocks and measurements for the geometry recorded scripts got wrong
(``performance.rst``, "Castle one-shot misses"). It has no privilege the
script lacks and runs under the same restore point.

The model's instructions do not describe the kit: neither Claude's nor Codex's
text names it (``agent_integration.rst``, "Toolset 39"), so the kit, its
checks and everything below that follows from them engage only for a script
that finds ``kit`` and uses it.

* ``kit.box``, ``kit.cylinder`` (``top_radius`` 0: a cone), ``kit.prism`` and
  ``kit.row`` make closed meshes wound outward by construction; ``kit.row``
  places its units strictly inside the span it is given and refuses a span
  shorter than one unit or an axis other than X or Y. Its ``unit`` (length,
  depth, height) and ``gap`` have no defaults: a default would be one request's
  sizes, given silently to every script that left them out.
* ``kit.cut(target, cutter)`` cuts only what it can prove
  (``script_kit_geometry.cut_mesh``). It first refuses, naming the reason, a
  target or cutter that is not a mesh in Object Mode or not a closed solid (an
  open edge, a wire edge or a loose vertex: what is inside is not defined), a
  target wound inward (a room's inside) or with shape keys, either one with a
  modifier that makes or moves geometry (an Array or Mirror would repeat the
  opening, a Solidify or Displace would move it, and one shown only in renders
  is refused too), a target something is bound to by vertex index (a vertex
  parent, another object's Surface or Mesh Deform or topology Data Transfer: a
  cut renumbers the vertices), and a cutter that another object uses. Only an
  explicit list stays on top and runs once: a Bevel, Weighted Normal and other
  shape-keeping modifiers, a subtracting or intersecting Boolean (it commutes
  with the cut), Shade Auto Smooth's node group and a Subdivision at level 0 in
  viewport and render. A stack that moves nothing at rest is no proof: a Mask,
  Armature or Hook leaves the cut's new vertices, in no group, behind. The
  refusal asks for a Boolean modifier at the end of the stack, or the person,
  rather than an applied stack. It then works on copies wound outward and cuts only the
  target's loose parts the cutter's bounds reach, so a far part is never
  re-cut, united or slowed; the EXACT solver handles self-intersection only when
  those parts overlap, touch or share faces. A result is kept only when the
  cutter overlaps them (relative to the smaller of the two), something is left,
  the target loses extent only where the cutter reaches and never grows, and
  the volume removed is the overlap's: what the parts lost, or, for parts that
  overlap, touch or share faces (whose own volume counts an overlap twice), the
  overlap less what the result still holds inside the cutter, read by a second
  intersection. A cut that fails one of those proofs without self-intersection
  handling is tried once with it (up to 50,000 vertices; a miss is final),
  under the same proofs. The volume proof runs with self-intersection handling
  too, so a crowded cut, or a retried one, whose difference removed nothing is
  not kept (reproduced with a stand-in difference in
  ``test_blender_script_booleans.py``; the crowded proof's intersection adds
  about 9 ms to a corner cut on a castle-scale wall, 15 to 24 ms). The result is written
  into a copy of the target's mesh, so every face keeps its material slot and
  the mesh its name and properties; only then does it swap the target's mesh.
  A cut first brings the view layer up to date, so an object moved just before
  is cut where it now is; a target or cutter Blender does not evaluate
  (disabled in viewports, in an excluded collection or another scene) is
  refused, since its position would be the old one. The cutter is then hidden in the view layer and in
  renders (out of the kit's rays and bounds, still evaluated, so it can move and
  cut again) and deleted when the script ends, ran or raised (``kit.finish``,
  called by the executor; its children keep their place): one cutter can cut
  several targets. Deleting it at its first cut would leave the script holding
  a removed object. A cutter the script
  took back (shown again, rendered, or used by another object) stays, shown in
  the viewport when it renders, and is named in the result
  (``kit_kept_cutters``). Every object or material the kit is given is checked to be still
  in the file before any Blender call (a removed one raises in Python). A refusal
  raises, so the script is rolled back and corrected like any raise, and
  changes nothing before it. On two threads a slot through a sphere of about
  160,000 vertices takes 1.5 s, and through a 10,250-vertex statue on a base it
  does not reach, 0.13 s. ``kit.arch`` cuts a round-arched opening with it and checks its
  crown, measured from below, under a name that carries its position, within
  1 % of its radius (plus the chord's sag for an odd number of segments).
* ``kit.check``, ``kit.check_bounds`` (world-space vertices of the evaluated
  objects, so a turned object is not read wide), ``kit.check_ray`` (a
  distance, or none for a clear path) and ``kit.check_supported`` record
  measured values beside their targets. ``kit.check_supported(name, prefix)``
  takes every loose part of the evaluated objects, curves and modifier copies
  included, and counts one supported when a chain of contacts within the
  tolerance (``tolerance=0.02``) joins it to the ground (``ground=0.0``):
  through the group's parts, whose bounds touch (hashed into a grid of
  part-sized cells, so a block of 9,000 parts takes 0.34 s), and through any
  other geometry the scene shows (``script_kit_support.py``): a car on a deck
  on piers, a cup on a table, a frame on a wall. Support is never one contact
  deep: a check of a car's wheels alone would pass a car in the air, because
  the wheels touch its body. Only the parts the group cannot hold up are
  searched, outward one ring of contacts at a time. Objects and instances
  (collection and Geometry Nodes instances included) count when they are
  visible, rendered and drawn solid, never the group's own objects or the
  instances they make. Contacts are read from surfaces, never from an
  object's bounds, which for a terrain or a room would hold everything inside
  them: a part touches an object when the part's grown bounds meet one of its
  triangles (an exact separating-axis test), two objects when a triangle of
  each meets the grown bounds of a triangle of the other (both ways: a
  low-poly slope's large slanted triangles have bounds that hold a deck
  floating above it); an object is grounded
  when its lowest point is within the tolerance of the ground or it touches a
  held-up part of the group. Parts that rest on the ground or on each other
  are recorded without reading the scene. A failure states
  the support looked for (``ground=`` and ``tolerance=``) and lists the
  floating parts lowest first, each with the nearest surface straight below it
  (an object, a part of the group, or the ground plane), the gap down to it
  and the lowest point its chain of contacts reaches (``reaches_down_to``: a
  car on a deck that itself floats reads gap 0 and the deck's underside).
  A prefix matches the objects it starts that are
  visible (not hidden, nor in a hidden collection). A check never raises and
  never passes vacuously: no target, a missing or non-finite value or
  tolerance, a prefix that matches nothing or a ray without a finite direction
  is recorded as failed with its reason, and one too large to measure (over
  two million vertices or 20,000 parts, the objects a support search reads
  counted in both, over 250,000 objects and instances listed for it, a contact
  test over 20 million comparisons, or 64 surfaces on a ray) with ``passed``
  null, which never passes. Values are JSON-safe (NumPy numbers rounded; NaN
  and infinity as null) and a list shows its first 12. A name repeated within
  a script gets " (2)".
* The kit has no colour check: a colour vocabulary would need an exception for
  one request's wording after another (``performance.rst``, "Concepts, not
  scenes").
* ``kit.parent(child, parent)`` (a child or a list, objects or names) parents
  without moving anything in the world (``script_kit_parent.py``): it brings
  the view layer up to date first, so a location set just before counts, then
  parents each child, sets its inverse from the parent's fresh
  ``matrix_world`` and puts it back at the world matrix it had; a parent of
  None unparents in place. It refuses, raising before any change, an object
  Blender does not evaluate in the scene, a parent that is the child itself or
  one of the child's children, and a parent without an inverse (a zero scale:
  the inverse is taken once, before any child changes). A child with an
  offsetting constraint (Copy Location with an offset) moves by that offset,
  as with Blender's own Keep Transform parenting. Parenting world-placed parts
  with ``body.matrix_world.inverted()`` right after setting the body's location
  leaves them offset by the move, because that matrix is not yet evaluated
  (`A parent's matrix read before its move was evaluated`_).
* Objects and materials may be given by exact name, a row's ends and centre
  line as points, an arch's centre as a point and a measured value as a vector.

The executor returns the record beside the script's own result:
``self_checks`` lists the first 32 passing and failing checks with their
values and further failing ones by name, up to 160; ``self_checks_passed`` and
``self_checks_failed`` name the others for the host only
(``model_results.SCRIPT_BOOKKEEPING``), so a fix that re-runs a long script
closes what it fixed and a failure the model was not shown stays open.

**A check is information, never a gate** (``self_checks.SelfChecks``). Tools
aid the model; they never handicap it. A gate that sends a plan back until
every check passes, holds every job while a check is open and fails the turn
when one stays open makes the model satisfy wrong checks: in recorded runs 118
of 170 failing checks were wrong checks on a right scene, 91 of them then
passed by copying the measured value into the target, and correct parts were
moved to satisfy a check (``performance.rst``, "Tools aid, never handicap").
What such a gate is for keeps its place:

* **A result round for what the model has not seen.** When a plan's own
  scripts record a failing check the model has not been shown (a new name, or
  new values), its results go back to the model before the turn can end, even
  after a plan that carried its reply and no ``verify``. A plan's reply is
  shown when the plan arrives, before anything runs; what the round guarantees
  is that the turn never ends on that reply while a check its scripts failed is
  unseen by the model. The round's first line names each such failure
  (measured value, target, tolerance, and a floating-parts check's parts and
  bounds), says the scene or the check can be what is wrong, and leaves the
  choice: fix the scene, fix the check under the same name, or reply as it is.
  A failure the model has seen already (the same check with the same values
  again) forces one more round only after a plan that carried a reply (so a
  fix that changed nothing is seen once), never a third, so re-running a
  failing check cannot loop a turn to its 16-round cap; a check that passed
  and fails again is news again. A plan stopped by a script that raised or an edit that
  failed sends back only its own note, within its own budget
  (``script_errors.SCRIPT_CORRECTIONS``,
  ``plan_recovery.FAILED_EDIT_CORRECTIONS``; rejected plans have
  ``plan_recovery.CLAUDE_PLAN_REJECTIONS``); a failed lookup's note comes
  first, then the checks'.
* **No mandate, no failed ending, no hold.** The turn completes whatever the
  checks say, and a job (``angelo_job_start``) or a ``LAST_JOB`` look starts
  where the plan puts it; a job's approval lists the checks failing at that
  moment (this plan's scripts so far included), as a fact.
* **Facts for the person.** A check stays open until a later script records it
  passing under the same name; within one plan a failure of a name outweighs
  its passes, and an unmeasured check never passes, fails or closes one.
  However the turn ends (completed, failed, refused past its budget, at the
  round cap, an observation that failed), as long as the request owns it, the
  person's notice lists the checks still open, once
  (``turn_endings.TurnEndings.around``): "The script's self-checks still
  fail", with each check's values, and no verdict.

A check without a measurement shows its reason in the rows. A floating-parts
check names the support it looked for, then its parts, what lies below each
and the gap, so a fix needs no diagnosis round. Passing checks change nothing:
no extra round.
``test_native_self_checks.py`` and ``test_native_turn_endings.py`` pin the
loop with mocked rounds and ``test_blender_script_kit.py`` (BACKGROUND) each
recorded miss and its guard (``test_blender_script_kit_support.py`` and
``test_native_script_kit_support.py`` the support check through the scene;
``kit.parent`` on the recorded car included; ``test_native_script_kit_parent.py``
pins its order without Blender).
A Codex thread's scripts get the kit and return ``self_checks`` too, which its
model reads in each script's result; neither runtime's instructions name the
kit, and only the Claude plan loop has the result round and the person's
notice.

A parent's matrix read before its move was evaluated
----------------------------------------------------

``child.matrix_parent_inverse = parent.matrix_world.inverted()`` keeps a child
where it is, but Blender updates ``matrix_world`` only when it evaluates the
scene: right after ``parent.location = ...`` it is the old matrix, and every
child lands offset by the move. Two recorded cars missed so: parts 1 m off the
body, or spread over 14 m. Before each such statement (any block, a loop body
on one line included) the executor runs a host call
(``script_prepare.Prepared``) that evaluates the view layer only when that
parent's matrix is stale, so the statement reads the parent's current matrix,
as Ctrl+P in Blender would; the statement itself runs as written. Staleness is
computed exactly for an object, and its parents, without keys, drivers or
constraints and parented to an object (its basis matrices composed); anything
else is evaluated, which is never wrong. A fresh parent is left alone, and a
loop over many children evaluates once. The result names the lines
(``evaluated_before_parent_inverse``) and that ``kit.parent`` parents in place.
The host call never fails a script, and line numbers in errors are the
script's own. The approved code is what runs, with that call before the
statement: it adds no privilege and changes nothing but the moment the scene
is evaluated, which Blender, the kit and many scripts do anyway.

Over the 517 recorded Angelo one-shot trials replayed in stock Blender 5.2.2,
it engaged in 3 scripts: the two recorded stale-inverse misses, whose cars
come out 4.0 x 1.8 m at y = -1 as built (against 14.15 m long, and y = -1.94),
and one passing trial whose later explicit inverse left it unchanged; no other
script changed. ``test_native_script_prepare.py`` and
``test_blender_script_parent_inverse.py`` (BACKGROUND) pin it. It adds no
instruction bytes; the note is in a result only where it engaged.

Removed Blender API
-------------------

Models still write Blender Python from 4.x that Blender 5.2 removed or renamed.
Such a script would run until that line raised, the host would roll the build
back, and the next round would write the whole script again. Validation reads every
reviewed script against a closed table (``script_api_removed.py``) and refuses
one that would certainly raise there, before it is approved or run, naming each
line and its 5.2 replacement; nothing has run.

* **The table.** Literal enum values (the sky type ``NISHITA``, now
  ``SINGLE_SCATTERING``, the same model, beside the new default
  ``MULTIPLE_SCATTERING``, in the same words as Blender's own error and the
  result note, a host test ties them; the engine ``BLENDER_EEVEE_NEXT``, now
  ``BLENDER_EEVEE``), attributes no 5.2 type has (``dust_density``, now
  ``aerosol_density``; ``use_auto_smooth`` and ``auto_smooth_angle``, now
  ``Mesh.set_sharp_from_angle``; the split-normals calls; EEVEE Legacy's
  reflections, bloom, ambient occlusion and shadow-size settings), the old
  OBJ, STL, PLY and Collada operators, and the Bump node's ``inputs['Invert']``
  (an ``invert`` property) on a node the script made as a Bump node. Each entry
  is a documented removal or a recorded raise;
  ``test_blender_script_api_removed.py`` (BACKGROUND) checks every one against
  Blender's own RNA and operators.
* **Only certain failures.** A value chosen at run time or compared, a name
  read inside a ``try``, or under a condition that asks for it (``hasattr``,
  ``in``, the name as text) is never refused: scripts that probe for either
  spelling run unchanged. Over the 2,618 recorded one-shot scripts of both
  sides, the check refuses exactly the 10 that raised on a removed name and
  none of the others (``test_native_script_api_removed.py`` pins the recorded
  shapes). It adds no instruction bytes.
* **Once per text.** A call's text is validated at admission, at each
  precondition and twice as it runs; its compile and this check are pure
  functions of the text, so ``script_source`` keeps them for the last 8 texts
  (the text is the key) and the executor runs the code object validation
  compiled. Text that does not compile keeps nothing.

Post-commit identities
----------------------

Every committed program result (an APPLY, or Accept of a PREVIEW) carries
``live_revisions``: for each written or created object, its document handle,
display name, type, role (``WRITTEN`` or ``CREATED``, with a created object's
local alias) and exact new ``object_revision``. The commit reads them inside its
Undo operator after the live writes and stage cleanup, through the same
``object_revision`` path that binding and commit re-run, so a later program can
bind these objects with no discovery query. Without them, a model that edits an
object it created, which is rarely active or selected, spends a whole round
looking it up again (see ``performance.rst``). The revisions grant nothing: any
later edit, or a selection or mode change, makes them stale and the next
program fails closed at binding or commit; a frame change does so for an object
that can change with time, while a still object's revision carries no frame
(``scene_intelligence.rst``, ``scene_fingerprints.context_field``). One
exception keeps an approved request alive: when only the current frame moved
between approval and commit (timeline step or playback) and every declared
input is frame-independent and provably unchanged, the same program is rebound
once at the new frame, and
staging waits while playback runs (``program_context.py``;
``fast_tier_reliability.rst``). Every written or created object gets one: the
read-back's cooperative budget (``revision_budget.READ_BACK_SECONDS``, 0.5 s of
the main thread's own CPU work, ``WorkClock``, as in the turn context, so
waiting on a busy machine does not omit them; checked between objects; the
first is always attempted and a small revision may use an allowance past it) bounds only a
pathological commit, and at most repeats once more the order of work the
commit's own revision re-check already did. A tighter budget would leave
revisions out and refuse the next program that bound them. An omission
states ``BUDGET`` or ``UNSUPPORTED`` with the closed limitation codes, as in the
turn context. A result names at most 96 objects (64 bound inputs plus one per
operation), and the field comes before the delta so a bounded transcript
handoff keeps it.

A model reads a compact copy of a finished program's result
(``program_model_result``, through ``model_results``): each object once, as
its live row with its world ``bounds`` to 1 mm (``object_bounds``) and counts
(and a light's or camera's settings) taken from the delta's geometry row,
matched by identity (an input's geometry row carries its handle, since its
name is the one before a rename; a created object joins the one CREATED row
of its name only when that name is unique); every check (a passed one as its
inner rows, observed numbers to seven significant digits) and every failure;
operation read-backs without the type and the counts the object's final row
states; no matrix, scope sentences, duplicate change rows or the constant
note. A preview's declared rows and note are kept as they are.
``detail`` names ``angelo_scene_object`` for an object's full transform and
topology; the chat's own copy (activity row, journal) keeps the whole result. A
failed result is shown whole. Measured on 138 recorded results:
``performance.rst``, "Heavy-scene payloads".

A PREVIEW result instead reports ``live_revisions`` with status
``UNCHANGED_UNTIL_ACCEPT``: its declared revisions, re-verified when the preview
was read back. The live scene is unchanged, so they still bind other programs
until something edits those objects; objects the preview would create have no
handle before Accept. Accept re-verifies the declared revisions and re-runs the
checks, commits like an APPLY and returns post-commit revisions.

The action catalog covers primitive creation, transforms, bounded BMesh edits,
allowlisted modifiers, simple materials/shader/geometry graphs, UV preparation,
parenting/constraints, explicit animation, rest rigs/weights, and cameras and
lights. Selectors use closed geometric rules or indices bound to a topology
generation. No arbitrary RNA assignment, callback, Python expression or shell
command is embedded in a typed action. Capability discovery reports the actual
native build support, including OpenSubdiv, rather than promising a missing
kernel.

Cameras and lights
------------------

``camera_create``/``camera_configure`` and ``light_create``/``light_configure``
(``action_camera_light_spec.py`` and ``action_camera_light.py``) write one
closed ``settings`` object per family, mapped to fixed RNA properties: camera
projection (PERSP/ORTHO), lens or orthographic scale, sensor fit/width/height
and clip range; light type (POINT/SUN/SPOT/AREA), energy, color, shadow, and
the type's radius, sun angle, spot size/blend or area shape/size. Every value
is bounded. Creation fills the documented type defaults (those of this build's
Add Camera/Add Light operators) so approvals show exact values; configuration
changes only supplied settings, and each type-specific setting must apply to
the resulting type. ``clip_start`` must stay below ``clip_end``. Kernels read
every written value back and fail if RNA did not retain it.

``look_at`` aims the local -Z axis at a world point with local +Y toward world
+Z and no roll, using an analytic XYZ Euler solve that is reproducible without
Blender. The kernel then checks the evaluated staged world matrix to within
1e-5 radians. It requires an unparented object without active constraints,
animation or delta rotation, and with positive scale; use ``transform`` to move
an existing object first. A configuration that only aims transfers only the
transform at commit.

``orbit`` (creation only, with ``look_at``, instead of ``location``) places
the new camera or light around its ``look_at`` point as the scene camera sees
it, in radians: ``azimuth`` 0 is the camera's side of the point, positive
turns toward the camera's right (counterclockwise seen from above) and pi is
straight behind the point, where a rim or back light stands; ``elevation``
rises from the point's height; ``distance`` defaults to the camera's own
distance from the point, so lights that leave it out stand equally far and
their energies alone rank them at the point. Lighting is conceived in the
camera's frame, and a world location for "behind" depends on where the camera
happens to be: in the factory scene a rim light at world +Y, (0, 5, 4), stands
about 102 degrees from the factory camera's view rather than behind the cube
as the camera sees it, even when the turn context states the camera's
position and view direction.

The host resolves an orbit once, before approval, as it expands a recipe
(``program_orbits.py``): the controller's first precondition reads the scene
camera's world position on the main thread (``BlenderExecutor.host_program``,
``program_camera.py``) and replaces each orbit with the exact location it names, rounded to six
decimals; the read counts toward the call's ``precondition_ms``. That resolved
program is the call's ``program_arguments``: the approval shows it, its derived
transform and aim checks certify it at commit, the transcript records it
beside what the model wrote, and the kernel creates an ordinary camera or
light (a kernel or a direct precondition refuses an unresolved orbit). An
orbit takes no ``location``: one written beside it, even ``[0, 0, 0]``, is
refused, and its canonical form has none until it is resolved.

Two properties of the frame are deliberate. It is built from the camera's
position alone, never its rotation: azimuth pi/2 is the right of an unrolled
camera at that position looking at the point, so for a rolled camera (a Dutch
angle, a portrait roll) or one nearly straight above the point it is a
world-level direction, not its screen right. And the location is fixed when it
is resolved, before approval: the camera is read, not bound as an input,
because binding it would also bind its parents and constraint targets. Nothing
re-places it after approval; a camera that stands elsewhere when the program
commits makes the commit refuse (below).

An orbit is measured from the camera the scene looks through once the program
has run, the one the person sees the light from: the scene's camera before the
program unless a ``property_set`` of the scene's ``camera``, anywhere in the
program, makes another one the scene's (a camera the program creates, at its
stated or resolved location, or an existing camera the host reads). An orbit
that places that very camera is measured from the scene camera at its own
point in the program, and so is every orbit of a program that leaves the scene
without a camera; such a program may delete that camera afterwards (commit
reads it before it goes) but not edit it or what it follows anywhere in the
program, since commit reads it after the whole program. A program that creates
a camera, makes it the scene's and
places lights around it resolves in one round, in any order and even in a
scene without a camera; measuring every orbit from the camera before the
program would cost such a program a correction round.

What moves the camera is predicted before approval, by the walk below, so that
a program that would move it is refused early with its remedy. The prediction
is not the guarantee; the commit check is (``camera_guards.py``). The resolved
program records what its orbits assumed in a host-written field,
``measured_from``: the camera the scene has after the program (a handle, a
``$alias`` or none) and, once per camera an orbit was measured from, its
reference and the world position it was measured at, read through the bound
view layer's depsgraph. The approval shows it with the program; no published
schema lists it, so ``tool_specs.validate_call`` refuses it from a model and
accepts it only as the host's (``host=True``). Commit reads both again, after
every write, inside the rollback scope of every other commit check. After a
program that writes the frame, or another setting after which Blender runs its
whole frame update (``program_frames.FRAME_UPDATE_PATHS``: every scene setting
whose RNA update sends the ``ND_FRAME`` notifier, the preview range, frame step
and time map among them, read from ``rna_scene.cc`` by a test), it first runs
that update (a frame-change evaluation at the scene's time, without
frame-change handlers, ``camera_guards.frame_update``), then up to three more
while the measured cameras or what the walk finds places them still change,
since Blender evaluates a dependency cycle one step per update; a placement
that has not settled is refused. The scene must then look through the assumed
camera (with Blender's camera-marker rule applied after such a program,
``BKE_scene_camera_switch_find``, at the scene's time through the time map),
and each measured camera's evaluated world position must be finite and the
measured one within float32 precision (four float32 steps of each coordinate's
magnitude, at least 1e-6), so a real move refuses far from the origin as near
it; a camera the program deletes is read before commit unlinks it, with
everything else written, and one it made and deleted stood where it was
stated. Otherwise the commit raises ``CAMERA_MOVED`` (``CHECK_FAILED``) naming
the camera, both positions, what moved it where the host can tell (an edit
after approval, seen in where it stood before the writes; a written parent or
constraint target; the frame update) and the fix that names (change it in
another program, write the frame setting in a program of its own, or submit the
program again to measure the camera where it stands), and the journal restores
everything the program wrote, followed by the one update Blender runs for the
restored frame. Blender's frame update replaces unkeyed values of animated
channels with their keys, as any frame change does, whether the program commits
or is refused (the rollback restores the frame through the same setting, and
Blender runs its frame update after the operator either way): before its first
write commit finds them by running the update a frame change runs, at the
scene's own frame, over every animated ID the view layer shows (Actions, NLA
strips and their own animated influence and time, and drivers), reading every
channel and writing the stored values back
(``animated_values.at_frame``), and names those the update replaced, in the
result when it commits (``unkeyed_values_replaced``) and in the refusal
otherwise, rather than claiming nothing changed; values that only follow their
keys to the new frame are not listed. That update is a time-source one, as a
frame change's is: tagging each animated ID as edited instead would outdate
the unbaked physics caches of every animated object. Each ID is listed whole or not at all (more than 4,096 channels, or
NLA strips deeper than they are read): when one is left out nothing is
evaluated before the writes and the result says the fact was not measured
(``partial``), since an ID evaluated whole but listed in part would lose the unkeyed
edits of its unlisted channels. Reading the cameras costs about 0.05 ms a commit; the extra
update runs only for programs with orbits that write a frame-update setting
(about 50 ms on a 20,000-object scene). With the walk stubbed to
"still", each of 28 adversarial orbit programs that would commit a wrong edit
without the check is refused this way, 11 valid ones still commit, and
``bpy.data`` is as it was after every refusal, except
the evaluated matrix of one camera whose driver reads its own rotation through
the active scene, a relation Blender does not build, so its evaluated value
lags one update before and after; wrong edits through frame-update settings, a
far camera moved 0.6, a camera at infinity and a cycle returning to its
measured spot after five updates are refused too. Outside the
check: frame-change handlers (an add-on's or a trusted file's Python), which run
when Blender processes the frame change, as for any frame change, and a
dependency cycle Blender leaves stale after a commit that runs no frame update.

Refused before anything is staged as ``VALIDATION`` (which claims no change
and leaves the request free to correct it), each naming the orbit, the
operation and what to write instead, only where the camera's position cannot be
known before the program runs or names no side: no camera to measure from; an
operation of the program that edits that camera or writes an object it follows
(its parent chain and constraint targets, and theirs, which the executor
reports; past 256 of them every orbit measured from it is refused), or a camera
the program makes in a way that states no position (a batch copy); orbits
placing each other's camera; a camera whose position is not finite, that stands
at the point, or straight above or below it (no side); and an orbit that lands
past the location bound of 100,000 (a far point with the default distance,
which is the camera's), naming the orbit and asking for a shorter distance. A
program that moves the current frame is refused an orbit measured from a camera
that moves with time, since commit evaluates it at the new frame; and a
frame-only rebind after approval is refused for a call whose orbit was measured
from such a camera (``orbit_moves_with_time``). A camera moves with time when
something animates what places it (``program_frames.placement_moves_with_time``):
its own keys, drivers or physics, or those of everything its parent chain and
constraints read (an armature's pose and bone constraints for a bone, a curve
or vertex parent's shape, a subtarget's mesh); a constraint that reads a shape
or the time itself (Follow Path, Shrinkwrap, the motion-tracking solvers)
counts too. One walker (``object_dependencies.walk``) finds what is read, for
this and for ``follows``: every Object or Collection a constraint, a modifier,
a data block or a Geometry Nodes group (nested groups and node sockets
included) points at, read from Blender's RNA rather than a list of attribute
names (a custom space object, a Mirror, Array, Hook, Curve or Lattice
modifier's object, a Geometry Nodes input or Object Info node, an instanced
collection), and what the drivers of every reached block read: their target
objects (by what the variable reads: its placement, its pose or shape, or
only its own properties for a custom property), every ID a variable's path
crosses, in order and a return to one already crossed included
(``active_material`` and its embedded node tree, whose keys count, an object's
data, a custom property that points back at the camera), the objects that use
a driven data block (one user map a walk; an embedded block is reached through
its owner) and a scene's settings, followed by the scene's handle and the
path's first step (a World or other block: the scene setting that points at
it), so only an edit of those settings refuses an orbit. A context variable's
path is resolved against each scene the camera is in (the active scene) or
each of their view layers, and every ID it crosses is walked as above. A
scene setting reads the time only when it is the frame (``frame_*``) or
cannot be named, or when a key or driver holds what the path reads; a static
World or render setting does not, so neither refuses every frame move. Paths
are read in Blender's grammar
(``dependency_paths.py``: identifiers, quoted keys with escapes, so a key may
hold ``]``, and indices); a path that does not parse, or that resolves while a
step of it cannot be read, leaves the walk incomplete, and so does one that
would stop short of everything (more objects than its limit, more user
map IDs or Geometry Nodes nodes than its budget), never a short
list; a walk stops at the first such point. A modifier counts
as frame-independent only when its type never reads the time and every Object
it points at is still; a driver reads the time only through ``frame``, a
Python expression or what its targets do, and a group two modifiers share is
scanned once. An orbit reads the camera's position only, so the targets of its
rotation-only constraints (Track To, Damped Track, Locked Track, Copy and Limit
Rotation) and its own rotation channels are left out when every active
constraint is of those, unless something the walk reaches can read the
camera's rotation or matrix back. Only these cannot: a location channel
(``TRANSFORMS`` ``LOC_*``, ``LOC_DIFF``) and a single-property path whose
steps in its target, up to where it leaves it, name no rotation and no matrix
(a lens through ``data``, a custom property's number); everything else does,
a world-space scale and an object the path reaches after leaving its target
(the same camera again included) among them. An object's
own pointers are its instancing (an instanced collection), never nested
settings such as light linking. The host reads both answers (whether the
camera moves with time and what it follows) from one walk per camera, so an
attribute list or a walk without drivers, node groups, path hops or
instanced collections can never judge a moving camera still. A Track To or a
parent on objects nothing animates does not move a camera, so an orbit around
such a rig rebinds on a timeline move. Any other way a
resolved program could fail validation is refused the same way.
``test_native_camera_guards.py`` and ``test_blender_camera_guards.py`` hold the
commit check and the path grammar, ``test_native_camera_orbits.py`` the factory scene's rig and
``test_native_typed_refusals.py`` the camera a program leaves
(``test_native_typed_review.py`` and ``test_blender_typed_review.py`` what moves
it with time);
``test_blender_camera_light_programs.py`` commits one in the binary and
measures it as the one-shot does; adversarial reproductions
(``test_native_review_light_rig.py``, ``test_blender_review_light_rig.py``)
assert the refusals above through the real controller.

Camera/light data are fingerprinted program inputs. A camera's depth-of-field
focus object is an object dependency that must be declared in the read set and
is remapped to the live object at commit. Configuration follows the existing
shared-data policy: staging edits a private copy, and commit gives only the
written object that copy (other users keep the original datablock); the
preview delta and commit result report it as ``shared_data`` and
``isolated_shared_data``. Animated or driven camera/light data, and lights
whose node tree is not Blender's default Emission -> Light Output graph, are
refused rather than configured into values that evaluation would override.
Solid previews neither draw cameras/lights nor shade with scene lights, and
the preview note says so; review the staged settings delta and checks.

Scene settings, including the active scene camera and the World, are written
by ``property_set`` and ``world_configure`` with the bound scene's handle as
target (see "Scene settings" below); timeline camera markers can still
override the active camera during playback and rendering.

Settings a call leaves out
--------------------------

A typed modifier's, constraint's and Principled material's settings take no
schema default (``action_schemas.SETTINGS_NOTE``, which the kinds' descriptions
carry): a setting or input a call leaves out stays as it is, and a new
modifier, constraint or material keeps Blender's own default for it, as camera
and light configuration does. ``modifier_configure`` writes only the settings
it states (``MODIFIER_RNA_NAMES``; an Array is a fixed count every time and a
Boolean's operand type goes with a stated operand, ``also_writes``);
``modifier_add`` and ``constraint_add`` write what they state, influence and
spaces included, over what Blender makes; a typed null keeps a setting as
leaving it out does, so both are one canonical program. ``material_principled``
naming a material its target holds sets the stated inputs of its one Principled
BSDF in place, every other input, node and link kept. It is refused, before
approval from the bound facts (``program_material_facts._bsdf``) and again by
the kernel, when the material has no Principled BSDF or several
(``PRINCIPLED_NOT_SINGLE``), a link drives a stated input (``INPUT_LINKED``),
or no render engine would show it (``PRINCIPLED_NOT_SHOWN``: it reaches the
Surface of neither the output Blender renders for EEVEE nor the one for Cycles,
``get_output_node``, through links and muted nodes' internal links); a link,
unlink or mute of an earlier step leaves those to the kernel. A new material gets a
fresh Principled BSDF at Blender's defaults with the stated inputs. Solid view
draws a material's display values (``diffuse_color``, ``metallic``,
``roughness``), never its graph, so every material path sets them from the node
result (``material_display_sync``; ``native_coverage.rst``, "Solid view
display"): a new material takes all of them, an
edit only those whose result it changed (a Roughness edit moves ``roughness``,
an IOR edit none). One rule, shared by validation,
the bound obligations and the kernel (``material_rules.definition`` and
``agrees``), says what a material is after each statement (an edit in place
merges into it) and whether a later statement on another object agrees with it:
each input it states must be one the material has, at that value. ``keyframe_insert`` without ``interpolation`` keeps a key already at the
frame as it is and gives a new one Blender's preference. Derived checks state
only what a call wrote (a modifier check without values checks the modifier and
its type; a later partial configure keeps the earlier values it does not
write), and the bound obligations model the in-place edit as a ``set`` of the
stated inputs on the bound Principled BSDF. Schema defaults would write over
the object's own values (a Bevel's width 0.2 would become 0.05 when a call
sets only its segments; a red metal given only a Roughness would commit grey).
``test_blender_kept_settings.py`` states each typed setting alone on every
typed modifier and constraint type, and each Principled input, and compares
every RNA value with Blender writing that one property.

What the host keeps
-------------------

Blender keeps an artist's unkeyed edit of an animated channel until it
evaluates that animation again (a frame change, an edit of its curves); keying
another channel or changing another setting does not. Staging evaluates every
fresh staged copy, a kernel that keys a channel tags its ID, and commit's
transfer of an edited Action evaluates the live object: each would replace
every unkeyed edit of the ID's animated channels, and commit would write that
to the live object with nothing said. ``kept_values`` reads every such channel before
each of those evaluations and writes the stored value back after, except the
channels the program itself has Blender evaluate (the ones it keys or whose
curves it edits, an Action or NLA it changes, every channel of a program that
moves the frame) and channels a driver holds; at commit the value is the one the
transfers stored, and after a failed commit's rollback (which gives each object
its Action again) the one it stored before the commit. A frame change's
replacements are stated (``unkeyed_values_replaced``), and so are the program's
own writes the next one will replace (``unkeyed_writes``). The list of kept
channels is built once per stage shape (its objects, aliases and owned IDs,
counted: a kernel that gives an object other data, materials or shape keys
tracks a new ID, and only a kind the program follows changes curves, on
channels it follows). Measured against the same build without
``kept_values``: unchanged for unkeyed
objects and for 2,000 untouched keyed ones, about 0.3 ms on one keyed object
(13 ms), 11 ms on a program that moves 30 keyed objects (290 ms), and 2% on a
batch of 1,000 copies (1 s). A known limit: an object a program duplicates or
copies keeps the stored values it was copied with, while Blender's first
evaluation of a new copy shows its animated values instead; the next frame
change gives it those.

Other host choices follow Blender too. ``keyframe_insert`` without ``frame``
keys the current frame, and a UV kind without ``uv_map`` uses the mesh's active
map (a new ``UVMap`` when it has none). A material edit in place that came to its object during the program
(assigned, copied or joined: the obligations model a join's slots as its kernel
appends them), a material the program created and shared, and a material whose
tree an Action, NLA or driver animates (``material_rules.animated``) are refused
before approval with what works instead (``material_rules.shared_in_program``,
which the kernel raises with the same words), not after it. So is an edit in
place of a bound material whose edited copy another object holds now (it was
assigned, copied or edited for all users after an earlier edit), and an
``ISOLATE`` of one another object got in the program: otherwise the kernel
would copy the material for the one object silently and the program would
fail its own checks after approval. Exactly those programs are refused; every
other shape commits (``program_material_sharing_replay``, which holds the
replay of who holds a material's staged copy). An ``object_delete`` of an
object that a datablock kept by its fake user points at is refused when the
program is bound (``program_refusals``): no kind changes such a pointer, so
commit's in-use check would refuse it after approval. An input counts as
driven by a link only through an enabled, valid one (``material_rules.driven``):
a muted or invalid link leaves its value in use. A constraint's, modifier's, node's or
particle system's own ``track_axis`` or ``rotation_mode`` is a setting of that
struct, not the object's transform (``rna_rules.screen_path(struct=True)``); a
node's ``location`` stays editor state, like its selection. A
linked-library material edited in place becomes Blender's local copy, which the
result states (``library_copy``), naming the library's own data that keeps the
library's material; that data is no user an edit changes, so it neither refuses
the edit nor is named as edited (``material_sharing.affected``). An object whose drivers Blender evaluates
natively (Average, Sum, Minimum or Maximum over transforms, distances, rotation
differences or stored properties; ``driver_rules``) binds and commits like a
constrained one, its drivers part of its revision and their targets declared
dependencies; a scripted driver, a context variable or a Python-defined property
anywhere on a variable's path (an index into one too) still makes it
unbindable.

Referring to objects, data and values
=====================================

A model writes a reference or a value the same way in every schema it reads:
Claude's plan, a Codex thread's response format, reference and tool
declarations, and every exact lookup. ``test_native_schema_consistency.py``
walks them all and fails on a form one place takes and another refuses, on a
form a schema offers that the host refuses, and the reverse, and on one
quantity bounded differently in two places.

* **An operation's or check's own object** (``target``, a binding's ``handle``,
  a tool argument naming an object) is its handle: ``o<n>`` where the
  conversation numbers handles, else ``scn:<epoch>:<uid>``, or the ``$alias`` of
  an object an earlier operation of the same program created. Never a name;
  never ``{"object": ...}``.
* **A field naming another object** (``parent``, ``mirror_object``, a Boolean
  ``operand``, a Shrinkwrap's, Curve modifier's or constraint's target, a
  marker's or the scene's ``camera``, ``source``, ``sources``, ``also``,
  ``armature``, a parent check's ``expected``) takes the handle or ``$alias``
  alone or as ``{"object": handle}``, the form read-backs show. The host stores
  the handle alone (``action_schema_types.POINTER``), so both forms make one
  canonical program, with the same declared dependencies, derived checks and
  approval.
* **An open settings map** (``property_set`` values; generic modifier,
  constraint, particle and pose-bone settings; the checks of them) is typed by
  RNA when the program is staged, so an object there is stored as
  ``{"object": handle}``. The host stores a string there that way only where it
  can name nothing but an object: a long handle, a short handle the
  conversation was shown, or the ``$alias`` of an object created earlier in the
  program (``open_values``). Any other string stays text, so ``"$5 off"`` or a
  name is never taken for an object, and a text setting given one of these
  forms is refused by its type rather than written. A check's map is admitted
  before RNA types it, so text there at an object pointer (a name, or an alias
  nothing declared) never passes: it names no object, the check fails and its
  read-back lists the setting as ``unresolved`` (``rna_rules.pointer_matches``).
  Maps that never hold an object (node
  sockets and settings, F-Curve modifier settings, operator properties) offer
  no ``{"object": ...}`` and refuse one (``NO_POINTERS``).
* **Other datablocks** go by exact name (materials, collections, node groups,
  bones, modifiers, constraints, UV maps; at most 64 characters), except an
  existing Image or Action, named by the ``dat:<epoch>:IMAGE:<uid>`` or
  ``dat:<epoch>:ACTION:<uid>`` ID a read shows, since names of those repeat
  across libraries and files; an ``nla`` check states the expected Action's
  name. ``material_assign`` takes a material from an object (``source``) or one
  the program states, never from the file by name: a declared object's
  revision covers its materials and a name covers nothing, so a file material
  no object uses is assigned by a reviewed script, as its refusal says.
* **Values** are JSON. A vector is an array of three numbers (lengths in
  metres, angles in radians, each field's space in its description); a colour
  is RGBA, four numbers 0-1, except a light's and the World's colour, which
  Blender stores as RGB. A number is a JSON number (a string is refused), an
  integer field a JSON integer, a boolean ``true`` or ``false``. Enum values
  are Blender's identifiers as Blender spells them (``BEVEL``, ``rotation_euler``,
  a node's ``Linear``); ``node_edit``'s step verbs and recipe names are
  lowercase. A nullable field left out is ``null``. One quantity has one bound
  wherever it is written: a location 100,000, a scale 1,000 (a transform's
  result too), an orientation 2 pi. ``keyframe_insert`` is a union by channel
  (its ``property``), so a key is bounded as its channel is, except that a
  rotation key may spin past 2 pi (several turns), and a mistake names the
  chosen member's field. The ``simple-animation`` recipe keys location and
  rotation: a scale variant would cost every plan and declaration 0.4-1.4 KB,
  and ``keyframe_insert`` keys a scale.
* **No check requires a tolerance.** Left out, a check compares each number at
  its own float32 read-back precision (an ``aim`` within 1e-4 radians), as the
  host's own derived checks do (``check_defaults``): one tolerance from a
  check's largest number would let a 1,000,000 W light's spot size pass 0.4 radians
  wide. A stated tolerance no larger than 0.00001 proves the derived check of
  the same values, which then runs once. A ``material_values`` check states any
  of its three values.
* **A strict grammar shows the stored form.** A Codex response format writes
  each pointer as the handle alone (``action_schema_types.stored_forms``):
  constrained decoding can write nothing its grammar lacks, so nothing there is
  refused and the other form would only cost bytes on every turn.
* **A mismatch names its own field.** Claude Code checks each plan with Ajv and
  answers with every error; a ``oneOf`` reports every member's, which can come
  to over 20,000 characters, cut to the first and last 5,000, and the cut can
  drop the cause. Claude's plan writes
  its tagged unions (tools, action kinds, checks, modifier and object types) as
  an ``if``/``then`` per tag value (``tagged_unions``), which accepts the same
  plans and answers a mistake in 150-500 characters naming the field
  (``test_native_tagged_unions.py``, with ``claude_code_errors``, which
  reproduces Claude Code 2.1.293's recorded camera error character for
  character: Ajv's order, and a text cut only past 11,024 characters). One
  residual is kept: an ``if`` has no ``required``, so an operation without its
  tag key (``type`` for ``kind``) is checked against every member, 7-10 KB.
  Ajv lists the missing tag last and Claude Code shows that much whole, so the
  model reads the cause; ``"required": [tag]`` in each ``if`` would cost 1,380 B
  on every plan, and none of 65 recorded refusals was a missing tag
  (``test_native_schema_review.py`` pins both).

Scene settings
==============

``property_set`` writes a closed allow-list of RNA settings
(``action_properties.PROPERTY_PATHS``, 150 paths) on one owner per operation:
an object, its camera, light or curve/text data (``data.`` paths), or the
bound scene (render, EEVEE, Cycles, colour management, frame and preview
ranges, units, gravity). ``test_blender_property_paths.py`` checks every path
against the installed RNA (type, array length, writability, the declared
bounds inside RNA's hard range, each enum item assignable on some fitting
owner); only Cycles paths may be missing, and ``angelo_scene_settings`` lists
them as unavailable. The scene
binds by the ``handle`` and settings ``revision`` from ``angelo_scene_settings``
(``scene_intelligence.rst``); bind and the pre-commit verification recompute
the revision, so a manual change to any setting ``property_set`` can write
fails the program closed. The revision digests the allow-listed paths it
reports, the proven Cycles settings and every other scene path the generic
rules admit (``scene_settings.generic_values``: about 280 more in Blender 5.2,
such as ``render.border_min_x``, ``eevee.gi_diffuse_bounces`` or
``sync_mode``, read along a plan ``rna_rules.writable_groups`` builds once
from the installed RNA), which it does not report: a change among them is an
opaque recovery field (``generic_settings``) that the next observation cannot
explain by value, so the model observes again. Digesting only the reported
paths would let a stale binding overwrite a manual change to another admitted
path. ``test_blender_property_set.py`` walks the
scene and checks that every admitted path is covered, except the current
frame. ``frame_current`` is a scene setting (``rna_rules.SCENE_ROOTS`` holds
every writable top-level setting of Blender 5.2's Scene RNA but ``display``,
which an Object also has, the sub-frame forms ``frame_float`` and
``frame_subframe``, and the ID flags), and it is also every program's bound
frame (``program_context``), so the settings revision leaves it out
(``scene_settings.UNREVISIONED_ROOTS``) and a timeline move stays a recoverable
frame-only change, except for a program that writes it: there a timeline move
after approval is that program's own setting changed (``STALE_SETTING``,
``native_programs.rebind``), never a rebind that overwrites the artist's move.
Every object's revision includes the frame, so a commit that moves it says so
(``frame_moved``): revisions observed before it bind nothing the result does
not list. Every check is measured at the frame the program leaves, and
Blender's frame change runs after commit, so a program that moves the frame,
or writes another setting after which Blender runs its whole frame update
(``program_frames.FRAME_UPDATE_PATHS``: the preview range, the frame step, the
time map), is
refused when binding (``program_refusals.check_frame_move``, before approval)
where the move would change what was checked: timeline markers that bind
cameras (``FRAME_CAMERA_MARKERS``: the frame change would make one of them the
scene camera after the checks), and a value of an existing object or the
scene that an F-curve or driver holds and the program sets without a key
(``FRAME_ANIMATED``, naming the operation and the path: the animation decides
that value at the new frame; ``program_frames.unkeyed_writes`` lists what
``property_set`` (``world.`` paths included, through the check staging
applies), ``transform``, ``transform_apply``, ``origin_set``, an unparenting
``parent_set`` that keeps the world transform (parenting keeps it through the
parent inverse and writes no channel), a batch that moves its sources and the
configure kinds set, by their F-curve paths: a camera's or light's data
settings and ``look_at``, ``constraints["name"].influence`` and settings,
``modifiers["name"].`` settings (``action_schemas.MODIFIER_RNA_NAMES`` where a
typed setting's RNA name differs) and ``pose.bones["name"].`` channels; a
``rotation_mode`` change names the channel Blender's setter rewrites, the new
mode's). The camera and light kernel refuses at staging only the data
settings it writes that an F-curve or driver holds, as the pre-approval check
does: a clip_end beside a keyed lens commits. A
rename, a material, a key deletion or any other write no
channel holds stays admitted, and staging, which evaluates the program at its
frame, still refuses what the checks see an animation change. Objects the
program creates and keys stay admitted: their checks sample the keys' own
frames. Refusing up front is deterministic; proving the camera after the
frame-change notifiers would come after commit, outside its Undo step. The fix
named in each refusal is the same: write the frame or that frame setting in a program of its own.
These bind-time refusals are the early prediction; Blender's own evaluation is
the guarantee. Staging evaluates the program at the frame it leaves and its
checks read the result, and commit evaluates the live scene there again before
its live checks: with ``check_frame_move`` disabled, an unkeyed write
on a keyed channel beside a frame move (a transform, an object, curve-data,
camera or light setting, a modifier or constraint setting, a bone channel, a
World strength, a material input) is refused at staging in every case, with
nothing written. At the frame a program does not move, an unkeyed write on a
channel an F-curve or driver holds is refused by its kernel or check for
camera and light data, bones, object and data settings, Worlds and materials;
a ``transform``, ``modifier_configure`` or ``constraint_configure`` value
there (or any other admitted write: ``transform_apply``, ``origin_set``, a
move, a modifier or constraint re-created over the keys Blender kept for its
name) commits and holds until Blender next evaluates that animation (a frame
change), as an artist's unkeyed edit does, and the result states it as a fact
(``unkeyed_writes``: each channel the program changed, by ID, path and index,
its value, the value its animation gives under ``next`` and whether keys or a
driver hold it, with ``count`` and ``truncated`` past 32). It is measured, not
modelled (``animated_values``): commit reads every channel an animation of the
written IDs can write before and after its writes, asks Blender to evaluate
just those IDs' animation at the scene's time (a ``TIME`` tag, an edit's, on
IDs the program has already edited, and an update, which applies NLA tracks,
solo, ``use_nla``, slots, influence and empty curves as a frame change does),
reads the result and writes the committed values back; a written ID the list
had to leave out is not evaluated and the statement says ``partial``. So the fact never names an unanimated channel, one
the program did not change, or one whose animation gives the written value
(a hand-written model of those rules would be wrong both ways). Such a write
is stated, not refused; the other kernels' refusals stand. A frame-only
rebind after approval
(``native_programs.rebind``) proves an input's placement by Blender's
evaluation, since a revision holds the object's and its dependencies' world
matrices read at the new frame; its evaluated shape it proves only by the
static judgment (``frame_independent``). A
``property_set`` without a target edits the scene only when every path is a
scene setting (``frame_current`` beside render settings is a scene edit, not
an object's); one that is not, or an object's path with the scene handle as
target, is refused naming those paths (``scene_binding.check_target``,
``implicit_targets``), as ``MIXED_OWNERS`` names them. When a program binds the scene, staging copies every allow-listed
setting of the live scene into the private stage scene (the active camera only
when the program declares it), the kernel writes the stage scene, and commit
copies exactly the written paths to the live scene inside the Undo operator,
journaling each previous value for rollback. Object settings transfer path by
path; data paths travel with the staged data copy under the shared-data policy.

Every value passes the schema (type and the allow-list's bounds, which are
tighter than RNA's), then the installed RNA definition (type, read-only, hard
range, array length, string length, pointer type; enum items through RNA's own
assignment check, which also covers build- and context-dependent items such as
render engines and colour-management views). All values are read back after
all writes. A refusal raises ``PropertyPathError`` with a stable code
(``FILE_PATH``, ``SCRIPT_OR_DRIVER``, ``CUSTOM_PROPERTY_OR_INDEX``,
``ID_POINTER``, ``NOT_ALLOWED``, ``MIXED_OWNERS``, ``EMPTY``,
``PROPERTY_MISSING_IN_BUILD``, ``OWNER_TYPE_MISMATCH``, ``RNA_TYPE_MISMATCH``,
``RNA_READONLY``, ``RNA_ARRAY_LENGTH``, ``RNA_RANGE``,
``ENUM_ITEM_UNAVAILABLE``, ``STRING_TOO_LONG``, ``OBJECT_TYPE_MISMATCH``,
``RNA_REJECTED``, ``VALUE_NOT_RETAINED``, and for an active material other
users share or a library provides ``MATERIAL_SHARED``, ``SHARED_OUTSIDE_SCENE``,
``ALL_USERS_MATERIAL_ONLY``, ``SHARING_UNKNOWN``, ``LINKED_MATERIAL`` and
``ACTIVE_SLOT_CONFLICT``; see "Generic settings, modifiers, constraints and
nodes"). The result lists each path's value
before and after; the delta and commit result name the written paths; the
committed ``live_revisions`` include a ``SCENE`` row whose ``object_revision``
is the new settings revision, so a follow-up settings edit binds without a read.
``test_native_property_set.py`` and ``test_blender_property_set.py`` cover
acceptance, every rejection code, read-back, staging isolation, preview and
accept, stale revisions, verification and Undo/Redo.

``world_configure`` sets the World background colour and strength and an
environment texture with a Z rotation. A colour stated beside a sky or an
environment texture, which feeds the rendered background instead, is set on
the Background node under that link and as the World colour Solid and
Workbench views show, and the result says so (``color_unused``) until the
source is removed. A sky and an environment texture together are refused
(``ONE_BACKGROUND``): one input cannot take both. Only Blender's default graph (an active
World Output fed by one Background, optionally fed by one Environment Texture
through Mapping and Generated Texture Coordinate) is configured; any other
node, link, linked strength or animation is refused as ``CUSTOM_WORLD_GRAPH``
or ``ANIMATED_WORLD`` rather than rewritten. The settings revision includes the
World's complete exact RNA and node tree (bounded), so World edits bind
through the scene handle. A program that configures the World stages a private
copy of it; commit gives the bound scene the edited copy and other scenes that
shared the World keep the original (reported as ``shared_data`` and
``isolated_shared_data``). A scene without a World gets a new one.

Environment images follow an existing-datablock policy: the request names an
image already loaded in the document by its resource ID
(``dat:<epoch>:IMAGE:<uid>``, listed without paths by
``angelo_scene_settings``), never a file path. Loading a file is an external
read outside the staged transaction and belongs to the file and Studio tools'
approvals; a stale epoch or a removed image is refused (``IMAGE_EXPIRED``,
``IMAGE_NOT_FOUND``). Image datablocks never enter the stage bundle: the staged
environment node holds no image, the intended image is kept by session UID in
the stage's ``extras``, verification compares that image, and commit assigns
the live image and reads it back, so suspending or previewing never duplicates
an image. ``test_native_world.py`` and ``test_blender_world.py`` cover the
graph rules, images, preview and accept, shared and missing Worlds, stale
revisions, verification and Undo.

Generic settings, modifiers, constraints and nodes
==================================================

One set of rules (``rna_rules``) decides what a generic write may touch: a
dotted path of lowercase identifiers through nested settings structs of the
installed RNA, crossing only an object's ``data`` and ``active_material`` and
the scene's ``world``; the leaf must be a writable boolean, number, enum,
string or object pointer. Denied, fail closed: file paths (by subtype and
name), scripts, drivers and expressions, Python-defined (runtime) properties
whose callbacks run Python (except a few proven Cycles settings), read-only,
hidden and collection properties, matrices, datablock names, pointers to any
datablock but an object, editor, tool, animation and library state, and any
other bone than the one a ``bone_set`` names. Values
are JSON; an object pointer is ``{"object": handle}``, a declared dependency;
a handle written alone there is stored that way where it can only name an
object (see "Referring to objects, data and values" above).
Each value is checked against the RNA definition (type, array length, hard
range, enum items by assignment), written and read back.

``property_set`` and ``property_keyframe`` use these paths on their owner.
Material paths (``active_material.``) write the live material in place at
commit, as Blender edits a material, so they follow the material sharing rules
("A material is stated once and shared" below): binding records each such
material's users and the bound scene's objects that show it
(``material_sharing.property_facts``) in the program's bound obligations, and
the kernel refuses a material with any other user (another object or mesh,
another datablock, a fake user; never data no user keeps, such as the mesh an
earlier commit replaced: ``material_sharing.kept``, which the object-in-use
checks of ``object_delete`` and the script kit's cutters use too, since counting
it would refuse the next edit of a material in ordinary multi-turn work; nor a
library's own data, which keeps the library's material: every shared judgment
uses ``material_sharing.affected``) unless ``shared`` is ``ALL_USERS``
(``MATERIAL_SHARED``), a user outside the bound scene or another datablock even
then (``SHARED_OUTSIDE_SCENE``), and a material an earlier step edited or one
the program did not bind for that target (``SHARING_UNKNOWN``). A material that
came to its target during the program (a ``material_assign`` or ``also``, a copy
of an object) is refused for an edit for all users with the correctable
``ARRIVED_IN_PROGRAM``: its users were bound for the object that had it before
the program, which the refusal names as the target to edit instead. A material from a linked library file, or a library
override, is refused at binding, before any approval (``LINKED_MATERIAL``, a
correctable refusal that changed nothing, from the bound ``linked`` fact), and
again by the kernel before the private copy: an edit in place would not be
saved with this file. A program that sets ``active_material_index`` and
``active_material.`` paths of the same object, in one operation or in several,
is refused at validation (``ACTIVE_SLOT_CONFLICT``, ``program_rules``), and so
is one that writes ``active_material.`` paths of an object and also changes its
material slots (a slot operator, a material assignment after the write, an
existing material assigned before it), because the paths and their derived
check would name, or lose the write to, a material the program did not bind;
set them in separate programs. A ``material_assign`` with ``copy_as`` (or a
``material_principled``) into the active slot (``slot``: its index) before the
write is the advised route to a local copy of a library material and works in
one program: binding follows program order, so that write edits the copy; a
copy into another slot leaves the library material active and is refused as
``LINKED_MATERIAL``. Any material step, ``active_material`` write or copy on
an object holding a library override in a slot is refused at binding, before
approval, with no copy route (native staging cannot copy an override; edit it
in its library file). Keying ``active_material_index`` and the other settings
RNA cannot animate is refused at validation (``NOT_ANIMATABLE``).
``ALL_USERS`` is the only value spelled (left out, a
shared material is refused); the field costs 32 B in the Claude plan schema
and 20 B in the Codex reference, and belongs to toolset 39
(``agent_integration.rst``, "Toolset 39"): a session that never sends it
only meets the refusal. An edit for all users is named in the approval,
the result (``shared_edits``) and the post-commit revisions (role
``SHARED``), and so is data that holds the material while no object uses it
(an orphan mesh with a fake user: ``holders``); a user set that changes after
approval or staging changes the
bound obligations, so staging or commit refuses before any mutation. A
material the program made itself (``material_assign`` with ``copy_as`` into
the active slot, then the paths) has no other user and is committed whole.
World paths stage a
private World copy and write the live World at commit when only the bound
scene uses it; when other scenes (or a fake user) use it, the private copy
keeps the whole graph (images by marker, groups cloned, as for ``node_edit``)
and commit gives the bound scene that edited copy, reported in
``isolated_shared_data``, while the others keep the original
(``program_preimages.WORLD_SHARING``). A World that gains a user after staging
refuses the commit (``STALE_SETTING``). Staging records the live value of every path a
program writes, and commit refuses before any mutation when one changed
meanwhile (``program_preimages``). A modifier, constraint or node type without
a typed schema is generic: its settings are RNA paths on the modifier,
constraint or node (``generic_settings``, ``node_generic``), read back by the
derived ``modifier_values`` and ``constraint_values`` checks, and generic
modifiers, constraints, geometry nodes and lattices are fingerprinted by their
writable settings so the object stays bindable. Modifier types reading or
writing files, caches, particle instances, volumes, fluids, oceans or node
groups are denied; every other type has an evaluated-size factor (deforming,
reducing or generating from its settings; a particle system passes its mesh
through and is bounded by its own settings, below). Geometry nodes with a
geometry output need a size rule fed by literal inputs (Distribute Points on
Faces: its literal density times the surface area of its mesh, below); zones,
volumes, imports and object, collection and image readers are denied.
``angelo_blender_api`` with domain
``WRITABLE`` lists the writable settings of any RNA type, so schemas carry no
RNA detail. ``test_native_generic_settings.py``,
``test_blender_generic_settings.py`` and ``test_blender_property_paths.py``
cover them.

Allow-listed operators
======================

``operator_run`` runs one Blender C operator (``bpy.ops`` idname, properties
by RNA name) on one staged object, with ``EXEC_DEFAULT`` only. Admission is
deny-by-default in two layers. ``action_operator_spec.ALLOWED`` names every
admitted operator with a growth model: reviewed Edit-mode operators of the
``mesh``, ``curve``, ``armature``, ``lattice``, ``uv``, ``transform``, hair
``curves`` and text ``font`` modules, the Grease Pencil layer operators
(Object mode, ``OBJECT_MODULES``) and its stroke operators (Edit mode), the
``constraint`` operators (Object mode), the pose transform operators (Pose
mode, ``POSE_MODULES``) and an Object subset (vertex groups, material slots,
shape keys, shading, ``forcefield_toggle``, skin marks, visual-transform and
parent-inverse applies, origin and track clears). The transform, Grease
Pencil stroke, hair-curve, text, constraint and data-local Object families are
tabled in ``action_operator_families``; ``DENIED`` refuses by name the
operators that create objects, rename bones (Blender then rewrites animation
paths across the whole file), have superlinear output or time
(``grease_pencil.select_similar``), grow without bound
(``grease_pencil.convert_curve_type`` resamples at the stroke resolution;
``curves.attribute_set``, whose active attribute can be the curves'
resolution), carry an upstream defect
(``grease_pencil.stroke_merge_by_distance``), read a clipboard, the mouse, the
view, the 3D cursor or an asset file, step frames (motion paths), apply a pose
as rest, edit bone constraints, or touch other selected objects, and those a
typed kind covers with its own commit (``object.particle_system_add``:
``modifier_add`` and ``particle_set``; ``object.modifiers_clear``:
``modifier_remove``, since a failed commit could not rebuild Multires, bind,
hook, particle or cache data of a cleared stack). At run time
``action_operators.admission`` also refuses Python-defined, macro, modal,
blocking, cursor-dependent and internal operators, operators without Undo, and
any file, pointer or collection property. Each property is checked against the
operator's RNA definition (type, array length, hard range,
not hidden or read-only, no file subtype); integers outside a short list of
amplifiers (``CAPS``: cuts, steps, segments, subdivisions, repeats...) and
benign selectors keep their defaults. Hidden properties stay unsettable
except where a family names one: ``center_override`` (a transform's pivot, in
world space) and ``constraint`` (the target's own constraint a
``constraint.*`` operator edits, required; ``owner`` is pinned to the object,
so bone constraints are out). Transforms always get ``snap``,
``use_automerge_and_split`` and ``use_snap_project`` off and, unless set,
``orient_type`` GLOBAL and proportional editing off with fixed falloff, so no
value falls back to a tool setting; the ``VIEW`` and ``CURSOR`` orientations
and projected falloff are refused (``VIEW_STATE``). Values are refused the
same way, before anything runs, when their growth or time has no bound
(``REFUSED_VALUES``): Grease Pencil simplify runs FIXED only (resampling:
``UNBOUNDED_GROWTH``; adaptive, quadratic in a stroke's points:
``SUPERLINEAR``; merge: ``UPSTREAM_DEFECT``), and closing a stroke defaults
to adding no points (density matching: ``UNBOUNDED_GROWTH``). Bend, shear,
warp and vertex slide (cursor, view or mouse), text cursor moves
(``TEXT_CURSOR``: the cursor is not in RNA, so no revision sees it), shared
Grease Pencil material operators (``SHARED_DATA``), modifier bind operators
(``MODIFIER_BIND_DATA``) and operators that reach other objects are denied by
name.

The kernel works only on the private copy. It overrides ``window=None`` (so no
context member resolves through the artist's view layer) and passes the
stage's scene, view layer and the target as the only selected and active
object, plus the staged armature's bones in Edit mode or its pose bones in
Pose mode; area and region are
empty, so nothing reads the artist's view or 3D cursor. Edit-module operators
run in Edit mode on the target, pose operators in Pose mode, the Grease Pencil
layer operators and the Object subset in Object mode (Edit mode
when its poll needs it or a selection is requested); the target always returns
to Object mode. Transform operators dereference the
context window in their status-bar text even with ``EXEC_DEFAULT`` (a
window-less Shrink/Fatten crashes Blender), so only their call is entered in
the program's window, with the stage's scene, view layer and members entered
again inside it (``action_operator_modes.call_override``) and the stage's
snapping, auto-merge, attribute-correction and proportional settings pinned
for the call (``pinned_tool_settings``); with no editor area they pivot on the
selection's bounding-box centre, never the 3D cursor (a missing window is
``NO_WINDOW``). While any window runs an artist's modal tool
(``native_programs.interaction_running``), a transform's call would take over
that window's status bar and Blender's global transform state, so the program
waits: ``ProgramJob`` polls report ``waiting`` without hydrating the stage or
running the operation (``BlenderProgramBackend.operation_waits``) until the
tool ends or the call expires. The stage scene pins Auto Keying off (a new
scene takes the artist's preference), so no staged operator keys. Mode switches
use the Edit and Pose toggles (``object.mode_set`` also writes a pending
autosave of the whole file, stage included), each in an override of its own
with no window and the view layer resolved from the stage scene; the work after
a switch runs in a newly entered override, and the scene and view layer are
checked before every operator (``action_edit_context.switch_mode``,
``stage_override``, shared with the typed Edit-mode kinds). With two or more
windows a switch refreshes the other
windows' tool systems, which drops a Python ``scene`` override, so an
inherited stage view layer would meet the artist's scene in Blender's update
after the call (a crash) or resolve from the artist's scene (the work running
there); the newly entered override after each switch prevents both. Before
running: the estimated element count (base elements
times the operator's growth and its amplifiers) must stay within the native
mesh budget, an armature's estimated bones within the 1,024-bone evaluation
budget (naming new bones is superlinear), fifteen operators measured to take
time growing with the square of their input within a bound on elements times
selected elements, checked after the selection pass (``WORK_BOUNDS``,
``WORK_BOUND``; measurements in ``native_coverage.rst``), indexed selections
bind topology steps like the typed mesh kinds,
and paths that would free a staged datablock (removing the last shape key) are
refused. The work bound of ``grease_pencil.extrude`` is stroke points x
selected points; ``object.skin_root_mark`` has a recursion bound (2,048
vertices: a 10,000-vertex chain overflows the stack, ``RECURSION_BOUND``); a
constraint name the target lacks (``CONSTRAINT_MISSING``) and any
``constraints``-family operator (``constraint.*``, ``object.track_clear``) on
a stack with an Armature constraint (``CONSTRAINT_TARGETS``) are refused:
commit and its rollback rebuild constraints from RNA settings, which hold no
target list. Curve-type
conversions of strokes and hair are rated at three times their points. Text
selection is ALL (the whole body) or NONE (the cursor after the last
character). Grease Pencil and hair-curve objects with modifiers are refused
before any evaluation (``action_budget``): no bound exists for them. In Edit
and Pose mode the requested selection (ALL by default, NONE, KEEP or
mesh element indexes with a select mode) is applied in its own pass, then the
operator runs; Blender's resulting selection is kept and commits with the data.
After: every new ID is tracked and owned by the stage (a new object or any
datablock other than a shape-key block is refused), a freed ID is refused,
meshes are bounded and finite, curves, Grease Pencil strokes and hair curves
hold at most 100,000 points plus splines, layers or curves (``GROWTH_BOUND``),
and ``operator_state.data_digest`` (mesh attributes, offsets and weights by
bulk reads, other data by its complete RNA,
shape keys and object-level state) must differ from the digest after the
selection pass: an operator that changed nothing is refused (``NO_EFFECT``).
What commit cannot carry is refused rather than previewed and lost: a changed
modifier or constraint stack outside the operator's families
(``UNCOMMITTED_STATE``; Shade Flat and Smooth remove a Smooth by Angle
modifier), and, after an armature operator, changed
bone constraints, custom properties or custom shape transforms (``POSE_DATA``;
duplicate and symmetrize copy them, delete and dissolve clear targets of the
removed bones). A mesh whose connectivity changed starts a new topology step.
Stage validation refuses an ``operator_run`` whose new element order would
detach the live object's vertex-parented children or index-bound Hooks, or
whose removed bones have bone-parented children (``REINDEXED_DEPENDENTS``):
leaving Edit mode remaps those only for objects using the staged copy.

Commit swaps the object's data (shared data is isolated as for the typed data
kinds), except for ``forcefield_toggle``, which writes only object state, and
pose operators, which commit through the ``pose`` family (``transfer_pose`` of
every bone, recorded in ``posed_bones``; the pose selection does not commit and
animated or driven poses are refused). Armature operators commit the data and
the ``pose`` family of every bone, so duplicated and mirrored bones keep the
pose settings they copied. Constraint operators commit through the
``constraints`` family: before the typed constraint kinds append or configure,
``operator_state.sync_constraints`` gives the live object the staged stack by
name (removed, added, configured with targets mapped to live objects, ordered),
journaling the whole previous stack so a failed commit rebuilds it from its
RNA settings (``restore_constraints``; a stack with an Armature constraint,
whose target list those settings miss, never reaches it). Object operators
that change transforms commit the ``transforms`` family; none of them swaps
data. Commit then copies the object-level state operators set beside the data
(active material and shape-key indexes, shape-key display flags, force-field
settings, and the parent inverse that origin and parent-inverse operators
change) with a journal (``operator_state.commit_object_state``). The object
revision gains an ``operator_state`` field for what operators change that the other
fields did not read (active material and vertex-group indexes, shape-key locks
and timing, lattice point selection and weights, bone collections and head and
tail selection, mesh skin radii and marks), so a manual edit of those makes a
plan stale; hair curves are fingerprinted by their complete RNA
(``curves_data``) and Grease Pencil materials by their stroke and fill
settings. No check is derived; ``property``, ``shape_key``, ``uv_layer``,
``modifier_stack``, ``modifier``, ``constraint_values`` and ``bone`` checks
derived earlier on the same object are dropped. Refusals carry a stable
code (``operator_run NO_EFFECT: ...``). ``test_native_operator_run.py`` and
``test_blender_operator_run.py`` cover the rules, every module, PREVIEW and
accept, stale revisions, suspension between operations, Undo and Redo, and run
every admitted operator once on fresh private copies, which must leave the
artist's objects and Main unchanged. ``test_native_operator_run_safety.py``
and ``test_blender_operator_run_safety.py`` run every runtime refusal on a
real operator that breaks it, the bone and work bounds, and the pose and
reindex guards; ``test_blender_operator_run_windows.py`` (GUI) opens a second
window on another workspace showing the live scene and checks, for
``operator_run`` and ``uv_unwrap``, the stage scene at every operator and the
live tool settings, cursor and keys unchanged; it also runs a transform, a
stroke, a hair-curve, a text and a constraint operator with the live
snapping, proportional editing, auto-merge, cursor pivot and orientation
primed, whose exact results prove none was read.
``test_native_operator_run_families.py``,
``test_blender_operator_run_families.py`` and
``test_blender_operator_run_objects.py`` cover those families: pivots,
pins, refusals and bounds, PREVIEW and accept, stale manual edits, commit, Undo
and Redo, and a forced rollback of the constraint stack.

``create_object`` also makes a blank Grease Pencil object (``GREASEPENCIL``:
one active layer with a frame at the current frame), whose layers
``operator_run`` then edits; its revision hashes the Grease Pencil data's
complete RNA (layers, frames and drawings), as for curves.

Particle systems and scattered points
=====================================

A particle system is a generic ``modifier_add`` of type ``PARTICLE_SYSTEM``;
its per-system settings (seed, vertex groups) are that modifier's
``particle_system.*`` paths. Its ParticleSettings, a shared datablock, are
written only by ``particle_set``: RNA paths on the settings of one named
PARTICLE_SYSTEM modifier, under the generic rules above (an instanced object
is ``{"object": handle}``, a declared dependency), read back by the derived
``particle_values`` check. Staging gives every system of a staged copy its
own copy of its settings with Blender's ``particle.new`` (marked with the live
settings' session UID); ``particle_set`` writes that copy (copying it first
when a staged duplicate shares it, in the program's override of the stage's
scene and view layer, ``program_objects._override``) and unmarks it. Commit
gives the written object's live system an edited or new copy with
``ID.user_remap``, journaled so a failed commit gives the old settings back;
the copy takes the old settings' name when nothing else uses them, and the
replaced settings stay in the file as orphan data until it is saved. Settings another system or a fake
user also keeps are refused (``PARTICLE_SETTINGS_SHARED``) rather than edited
for objects outside the program; a duplicate the program creates gets the live
settings back unless the program edited them. ``ParticleSystem.settings`` is
never assigned through RNA: its setter counts users while the property is not
tagged as counting them, which trips Blender's reference-count assertion.

``action_budget`` bounds every system before evaluation (``particle_rules``):
parents (the count, or a grid's cells) times one plus the larger of the
viewport and render child amounts, times the points of each path for hair or
path display (``2^step + 1`` of the larger step, plus spiral kink steps) or the
trail length, plus one instanced object's evaluated size per particle, and,
for emitters, parents times the substeps of a frame (101 when adaptive) times
the frames Blender steps from a negative start; both must stay within the
evaluated-vertex budget. Keyed, boids and fluid physics, particles acting as
force fields, collections (instancing, collision, effector weights), textures,
animated settings, hair dynamics and disk or external caches are refused; an
existing system with any of them keeps its object out of native programs. The
object revision hashes each
system's ParticleSettings (complete RNA, by identity) and its instanced and
force-field objects as dependencies, so a stale plan is refused; edited
(combed) hair and the refused dependencies make the revision inexact.
``particle_set`` refuses edited hair and baked caches, and removing, applying,
joining or separating a particle system stays reviewed Python. The program
fingerprint skips point-cache status text (``info``), which playback rewrites.

Distribute Points on Faces is a generic geometry node with an area rule
(``node_generic.AREA_BOUNDS``): its literal density (Density Max for Poisson
disk) times the surface area of its mesh, plus one rounding point per
triangle. The area is the base mesh's triangulated area only when the Geometry
Nodes modifier is first in the stack; a Grid or Cube with literal sizes states
its own; nodes that cannot enlarge a surface pass it on; anything else, or a
linked density, refuses the graph before evaluation.
``test_native_particles.py``, ``test_blender_particles.py`` and the particle
cases of ``test_blender_program_commit_effects.py`` cover them.

Editing existing node trees
===========================

``node_edit`` (``node_edit_spec``, kernel ``node_edit``, steps
``node_edit_ops`` and ``node_copy``, commit ``node_edit_commit``) makes
targeted edits to one existing tree: a material on the target's slots (by its
live name), a Geometry Nodes modifier's group, or the bound scene's World
(scene handle target); ``group`` edits inside a group node's group of a
material. Up to 32 steps name nodes by their exact names, as observations
list them (``angelo_scene_object`` MATERIALS and MODIFIERS sections, and the
``world_graph`` node names and links of ``angelo_scene_settings``), and are
validated as a whole: add (any generic node type or a frame, RNA settings, input defaults,
an image by resource ID, a location, a frame), remove (optionally reconnecting
through the node's internal links, Blender's Delete with Reconnect), link,
unlink, detach, set (settings by RNA path, input and Value/RGB output
defaults), parent to a frame, duplicate, swap (Blender's Swap Node rules:
matching values and links kept, dropped links reported), insert on a link,
make group, ungroup and interface sockets of the edited group. The advertised
step schema is one flat object (an ``op`` enum, each field once and a note of
every op's fields); ``node_edit_spec.canonical`` validates each step against
its op's closed variant, refusing a missing field or another op's field
(``FIELDS``), so a step carries only its op's fields and defaults. New nodes are
never selected and take exactly the requested name; selection and the place of
existing nodes are never written. Blender's API has no node copy and its
editor operators need a Node Editor, which a program never creates:
duplicates, groups and ungroups rebuild nodes from their installed RNA
(settings, ramps, curves, pointers, sockets) and refuse a copy whose digest
differs (``NODE_COPY_INEXACT``).

Ownership and sharing. Edits run on the staged private copies: staging
already copies every material and node group of a written object, and for a
World edit ``node_edit_commit.prepare`` gives the stage scene a private copy
of the World (any graph), its nested groups cloned and its images kept on
their nodes by session UID, as ``world_configure`` does. ``prepare`` also
records, before the first bundle, how many users outside its target each tree
has (other objects or meshes, modifiers, group nodes, scenes, a fake user). A
shared tree is refused (``NODE_TREE_SHARED``) unless ``shared`` is
``ISOLATE``: then only the target's own references (its slots, its modifier,
the scene) receive the edited copy, and every other user keeps the original.
A slot linked to mesh data other objects share cannot be isolated
(``DATA_SHARED``). A group inside a material is shared when that material is,
and a nested edit always takes the material's edited copy too, so every slot
of the target holding it shows the same edited group (staging copies each live
material once per object, however many slots hold it). The edited copy carries the live tree's session UID
and its mode (``REMAP`` for a single-user tree, ``ASSIGN`` for an isolated
one).

Commit, after any data swap and modifier transfer: ``REMAP`` checks again that
the live tree has no other user (users gained since staging refuse the
commit), replaces every use of it with the edited copy (``ID.user_remap``)
and journals a remap back; ``ASSIGN`` points the target's references at the
copy, journaled. Then nested group clones nobody edited go back to their live
groups, images are assigned from their markers, the markers are removed and a
single-user tree's copy takes the original's name. The replaced tree stays in
the file as orphan data until it is saved. Nothing edits a live tree in
place, so the preview is what commit makes.

Validity and bounds. After each operation a tree keeps its output nodes, a
linked material or World surface stays linked, every link is valid and the
graph acyclic, within 256 nodes and 512 links; links are made only between
compatible socket types. A Geometry Nodes tree must stay a closed catalog
graph (``node_edit_geometry``): its provenance is regenerated from the real
nodes and links, validated by the catalog's rules and audited, and the
modifier stack is bounded again (``action_budget``, ``node_generic``) before
any evaluation, so raising a Subdivide level or a Distribute density over the
budget is refused. Frames, mutes, groups and interface sockets are outside
that contract and refused there; the World refuses nested-group edits and
ungroup, whose contents its revision does not cover. Animated or driven trees,
objects created in the same program and object pointers in settings are
refused.

Revisions. The object revision gains ``node_trees`` (``node_graph_check``):
every node's frame and natively writable RNA settings (except selection and
position) in the object's material trees and Geometry Nodes groups, nested
groups included, and each group's interface, so a manual edit after
observation makes a plan stale instead of being overwritten; frames do not
make a material unbindable. The World is covered by the scene settings
revision. ``test_native_node_edit.py``, ``test_blender_node_edit.py`` and the
node_edit cases of ``test_blender_program_commit_effects.py`` cover them.

Object management
=================

``object_rename``, ``object_delete``, ``object_duplicate``,
``transform_apply``, ``origin_set`` and ``collection_link`` follow the program
contract. Geometry changes (applied transforms, a moved origin, a full
duplicate's data) are made on staged private copies. ``transform_apply`` bakes
the matrix Blender's Apply builds from the channels (``transform_bake``): the
scale alone, rotation times scale, the rotation conjugated by the scale, and
the location through what stays; it resets only the applied channels and flips
the faces when that matrix mirrors, as Corrective Flip Normals does. A mirrored
scale applied alone keeps the rotation channels exactly; rebuilding the basis
from a decomposition would turn scale (-2, 1, 1) into a 180-degree rotation,
which a keyed rotation flips back on the next frame. Changes to the live
scene's structure are staged as intents in the stage's ``extras`` and carried
out by commit inside the Undo operator, each journaled for rollback: renames
(a taken name fails instead of being suffixed), linked duplicates pointed at
the live data of their link root (a program that edits linked data after the
link is refused before approval, ``LINKED_EDIT``, unless every member, made in
the program without material slots, gets the same material on every face; ``local_verification_and_recipes.rst``,
"Commit fidelity"), new objects linked into their staged collections,
collection links, unlinks, moves and created collections (an object always
keeps one collection of the scene), and deletions. A new object holds the
collections staging recorded for it in program order: a duplicate, batch copy,
conversion or separated part takes its source's collections at that point of
the program, as Blender's Duplicate, Convert and Separate do, and its own later
``collection_link`` edits change them. Commit creates the program's collections
first (in program order), then links each new object once into exactly its
staged collections, then replays each declared object's edits from its live
memberships (``program_memberships``); linking a copy into its source's
collections from before the program would put a copy made after its source
moved in the wrong collection. A collection a new object joins that is renamed or removed before
commit is a stale dependency (``COLLECTION_NOT_FOUND``, ``STALE_DEPENDENCY``),
and so is a collection the program creates that the bound scene gained before
commit (``NAME_TAKEN``): commit never adopts the artist's collection as the
program's own. Names mean local collections: a library collection of the
same name neither blocks creating a local one nor receives the program's
objects, and one with no local namesake is refused as ``VALIDATION``
(``LINKED``) while staging.
After every link and edit, commit checks that each new object
is in exactly its staged collections and rolls the whole commit back if not
(``verify_new_memberships``); a new object staging recorded no collections for
fails the same way rather than landing in the Scene Collection.

A new object, and every other ID a program creates with a public name (a
copied material, a new mesh, node group or Action), keeps its identity while
its stage is suspended (``stage_new_objects``). A suspended stage holds none
of its IDs in Main, so such a name is free while the approval waits; when the
artist takes it (a Shift+D of a sibling, a new object or material), the
staged ID comes back under another name, renamed by Blender as it is attached
again or loaded from a preview's private bundle. IDs are found again by
identity: the same IDs for a memory-backed suspension, and for a file-backed
one a marker only the private bundle holds, removed on load. The move is
recorded per ID type. The staged collections of an object without an alias (a
separated part, an unaliased create, kept by the name commit gives it,
``program_names.NAMED``) follow it, and commit corrects the result: object
fields follow an object's move and material fields a material's, never a
same-named ID of another type, and ``renamed_while_waiting`` lists every
created ID that committed under another name than results first gave it. An
unnamed duplicate, a part or a copied material simply commits under the name
Blender gave it, as it would if the artist had acted first; a requested
object name that was taken fails its derived ``object_name`` check before
commit (``CHECK_FAILED``, nothing changed), except a name an earlier creation
of the same program asked for: ``create_primitive``, ``camera_create``,
``light_create`` and ``armature_create`` then derive no name check and keep
Blender's next free ``.NNN``, which the result's ``object`` states (recorded
2026-10-25: a name asked twice cost three phase 1 workers a whole round each).
That exemption holds only for a name free before the program: stage creation
records the names two or more creations ask for that Main held then
(``extras["held_repeats"]``), and a creation of one of them that Blender would
suffix while its holder from before the program still holds it is refused on
the stage (``NAME_TAKEN``), whichever creation asked first (break-it E2E-19B:
an unaliased create or ``armature_create`` has no name check of its own). Fields naming other new IDs (a
node group, an Action, an image) are not rewritten; the note names them.

A deleted object is unlinked from the bound scene only when no other object, scene camera or other
datablock uses it (``bpy.data.user_map``); its ID is removed after every check
passed and the post-commit revisions were read, so nothing has to roll back a
freed ID. Applying transforms or moving an origin refuses objects with live
children or delta transforms, because either would move other geometry.
Applying rotation zeroes every rotation representation (Euler, quaternion and
axis-angle), as Blender's apply does, so its derived ``rotation_euler`` check
holds in any rotation mode.
Verification of a stage reads these intents (memberships, pending names,
deleted handles), since staged copies keep private names and stay in the
private stage collection. ``test_native_object_management.py`` and
``test_blender_object_management.py`` cover the kinds, refusals, Undo and a
forced rollback of renames and collection edits.

Object names follow program order, as Blender's own sequence of the same edits
would (``program_names.py``). A live object keeps its name in Main until
commit, but once an earlier operation of the program renamed it away or
deleted it, its name is free; a name a pending rename will give is taken,
although Main still shows it free; and a deleted object that another scene
still links keeps its name, as Blender's delete leaves it (recorded when the
stage is created). A model writes a plan in the order of the person's
sentence ("add a camera named Camera ..., then delete the old one"), so a
request by name may also take at once a name every declared holder of which
the program renames away or deletes later (``plan_names``, before staging);
when such a holder keeps the name after all (renamed back, or deleted while
another scene links it) or a second operation asks for the same name, the
plan is refused before staging with the reason (``NAME_AMBIGUOUS``). Every
kind that names an object asks for the name Blender would give at that point:
the requested name when free, else the lowest free ``.NNN`` of its base. ``create_primitive``, ``camera_create``,
``light_create`` and ``armature_create`` take that name; ``object_rename``,
``create_object``, a named ``object_duplicate`` or ``object_convert`` and
``batch`` refuse ``NAME_TAKEN`` instead of a suffix. Unnamed duplicates,
conversions and separated parts are named after their source's current name
(an in-place ``object_convert`` keeps it). A new object given a name that Main
still holds for a released holder keeps a fixed-length private placeholder on
the stage and a claim (``extras["name_claims"]``, and its alias in
``live_names``); checks, results and the delta read the claimed name, and a
check may never expect a private ``__angelo_`` name. The slot a first key
creates is named after the object's live name or claim
(``last_slot_identifier``), never its private staged name. Commit first moves
each renamed or removed holder of a name the program gives away to a
temporary name, then gives every rename and claim its name, all journaled for
rollback; a rename of an object the commit removes is skipped, and a name
taken since staging fails the commit. The production commit scope approves
exactly the deletions the program's own operations make (``object_delete``,
``object_join``'s sources and ``object_convert`` without ``keep_original``).
Its native proof (``angelo_commit_rights.cc``, ``prove_deletion``) refuses a
deletion that a surviving ID still depends on, but treats an editor cache that
is never written to a file (``IDWALK_CB_WRITEFILE_IGNORE``, owned by a screen:
the Outliner's tree-store) like the weak editor links: the stock deletion
clears it through its editor remap. Window-manager and workspace links get no
such exemption. Without it, every Outliner that had drawn an object would keep
it: in the app an approved deletion would be retained ("approved objects
retained in data") and reported as a cleanup warning, while background suites,
which draw no Outliner, would pass.
``test_native_program_names.py`` replays the benchmark plans,
``test_native_program_names_fuzz.py`` compares seeded programs with a model of
Blender's naming, and ``test_blender_program_names.py`` runs the scenarios
through the QA commit and the production Undo bridge.

``batch`` makes the copies of a repeated unit the way ``object_duplicate``
makes one, in a single operation: each copy joins its source's collections
at that point of the program,
keeps its modifiers, constraints and parent (a part parented inside the unit
follows its own copy of the parent) and takes the next free name of its source's
numbering: a name ending in a number continues it ('Post 01', 'Post 02'), and
any other name keeps itself with Blender's copy suffix ('Pier', 'Pier.001'),
the name Shift+D or a script's ``Object.copy()`` gives. The copies' block of
numbers starts after the highest one held inside it, so copies of 'Cup 1'
beside 'Cup 2' to 'Cup 7' are 'Cup 8' onward instead of a NAME_TAKEN refusal
(recorded: a 90-object scene where the model then named each copy itself). A
name any operation of the same program asks for by name is held for it
(``extras["requested_names"]``, ``program_names.copy_name_taken``), so copies
of 'Plank 0' skip a 'Plank 1' the program creates later instead of making it
'Plank 1.001' and failing its name check (recorded 2026-10-25, two phase 1
workers); only
a name longer than Blender's 63 characters is still refused. A ``copies`` check
outside the program finds the copies by the result's ``first`` and ``stride``;
without them it is inconclusive rather than reading older objects of the same
numbering as the copies. Any other numbering
(a second 'Bridge Abutments' as 'Bridge Abutments 2') would give a name an
exact-name lookup for Blender's copy does not find. A linked copy shares its source's data; for a declared source,
commit points it at the live data, as for a linked duplicate. Placement is in
world space and nothing is evaluated: ``at``, ``along`` and ``scatter`` are
positions of the unit's anchor (the first source's origin) and the unit itself
moves to the first, so n positions make n-1 more instances (2n-1 with
``mirror``), each copying every source, while ``array`` steps are offsets and
each count includes the unit. Models read that sentence
(``batch_spec.PLACEMENT_NOTE``) in the Codex program reference, in an exact
lookup and, in Claude's plan, where ``batch`` is exact, as the field's
description, because a schema cannot say it: given only the types, a model
can write ``at`` as offsets and make fewer copies than asked. The note also
says that copies continue the unit's name and
alias (``$post_2``) and take what the unit has when the batch runs, so its
material and modifiers go before the batch. A
batch that places or varies its unit writes the sources, so they are bound in
the write set. Linked copies of data
created in the same program cannot be edited later in that program: the
refusal says to edit before copying. Every refusal the kernel makes is
correctable, as ``object_duplicate``'s are: a source of another type (a Grease
Pencil object), a parent chain whose place needs evaluating
(``PARENT_SPACE``), shear and an animated source are ``VALIDATION`` or
``UNSUPPORTED``, the copy budgets ``LIMIT_EXCEEDED``. ``native_coverage.rst`` ("Batch work
bounds") has the bounds and measured cost; ``test_native_batch.py`` and
``test_blender_batch.py`` cover placements, units, mirroring, variation, Undo,
Redo, a forced rollback and a stale binding.

A material is stated once and shared. ``material_assign`` puts a material
that a staged object already has on other objects (by exact name on
``source``, the target or an earlier operation; never by a lookup in the
file), ``also`` on the material kinds adds objects to the same assignment, and
a program that states one name twice with the same values, or with no values
the second time, reuses the datablock. Each object's slot receives the staged
copy; commit swaps an unedited copy back for the live material, so the objects
share one datablock. ``shared: ALL_USERS`` edits a material's own tree for
every object that uses it when all of them are objects of the bound scene: the
staged copy records its users, commit refuses if the live users differ, and
the approval (``shared_edits`` in the reviewed program: the other objects, at
most 32 names, and their count, from the bound facts), the result and the
post-commit revisions name them. An edit in place leaves
mesh data unswapped, so instances keep sharing it. ``property_set``'s
``active_material.`` paths take the same ``shared: ALL_USERS`` ("Generic
settings, modifiers, constraints and nodes").
A staged copy of a live material is named with a suffix (``Stone.001``) while
the live ``Stone`` exists; every lookup, registration and result names it by
the live material's name, found by the session UID the copy carries, on
whichever object holds it (``node_edit.logical_name``). The sharing facts of a
``node_edit`` target are only one source of that name: a source a
``material_assign`` reuses, an ``also`` object or an alias has none.
Registering a copy under its private name would make a second reuse of the
same source in the same program refuse ``MATERIAL_NOT_FOUND``.
``test_native_material_sharing.py``, ``test_blender_material_sharing.py``,
``test_native_material_identity.py`` and ``test_blender_stage_id_names.py``
cover assignment, reuse, copies, edits for all users, Undo, Redo and recovery.

Deleting the bound scene's active camera clears the scene's camera as part of
the delete, as Blender's own delete does. Commit refuses to remove an object a
scene still looks through, so that no scene's camera changes outside what was
approved; a program that also sets the scene's ``camera`` (to another camera
or null), anywhere in it, commits as written, since commit writes scene
settings before it removes objects. A program that sets none gets that
``property_set`` from the host: the controller's first precondition reads the
live camera (``program_camera.host_program``) and, when the program deletes it,
appends ``{"camera": null}`` on the scene, with the scene in the write set at
the revision the program declares for it, else the live one
(``program_refusals.release_camera``). The approval shows the added operation,
its derived check certifies it, Undo restores the camera, and the result lists
the scene's ``camera`` among the changes, so a typed program that clears the
default scene commits without a correction round. Binding still refuses
(``ACTIVE_CAMERA``) what the completion cannot release: a camera another scene
uses, a program that makes the camera it deletes the scene's, and a program
bound without the completion (an executor that cannot read the camera, or no
room: 32 operations or 64 inputs). ``test_native_typed_refusals.py`` and
``test_blender_object_management.py`` cover it.

``object_join`` merges declared source meshes into its target's private mesh
in the target's local space (world placement is kept; a mirrored source's
normals are flipped) and maps each source slot to the target slot holding the
same live material, appending missing ones. Its sources are written, not read:
they must be in the write set and are deleted by ``object_delete``'s commit
path, so a source another declared object still uses, or one with children, is
refused. Source modifiers are not applied, as in Blender's Join; vertex groups,
shape keys and object-linked material slots are refused rather than merged
approximately. ``mesh_separate`` splits its target by loose parts, material
index or the selected faces: the target keeps the first part and each other
part (at most 64) becomes a new object, a copy of the target holding only that
part, named after the live object (``Cube.001``) and linked at commit into
the collections the target had when it was separated (``program_names.NAMED``).
``test_native_join_separate.py`` and ``test_blender_join_separate.py`` cover
them.

``curve_points`` replaces a curve's splines with one poly, Bezier (auto or
vector handles) or NURBS spline through up to 256 explicit local points on the
private curve copy; commit swaps the curve data. ``object_convert`` is a
create kind: it evaluates a curve or text object on the stage and adds a new
mesh object (``$alias``) with its source's transform and parent, linked into
the collections the source had when it was converted, as Blender's Convert to
Mesh with Keep Original does; ``keep_original: false`` also deletes the source,
which is then written and must be in the write set. An estimate from the curve settings
bounds the conversion before evaluation and the mesh limits after it. Curves
with modifiers or bevel and taper objects are refused. ``test_native_curves.py``
and ``test_blender_curves.py`` cover them.

``shader_nodes`` accepts a ``TEX_IMAGE`` node naming a loaded image by the ID
``angelo_scene_settings`` lists (no file paths). The staged node carries only
that ID, because a suspended stage would otherwise copy the live image into its
private bundle; commit assigns the live image (and fails, rolling back, if it
was removed meanwhile). A named graph derives a ``material_assignment`` check.
``test_blender_material_images.py`` covers it, including a forced
suspension.

``visibility_set`` hides or shows an object in the bound view layer (the eye,
``Object.hide_set``) or, with the scene handle as target and a collection
name, sets the view layer's ``exclude`` and eye for that collection and the
collection's own ``hide_viewport``, ``hide_render`` and ``hide_select``. An
object's own disable flags stay ``property_set`` paths. Both are view-layer or
scene state outside the staged copies, so the kernel records intents (and
hides the staged object on the stage's view layer for previews) and commit
applies them to the bound view layer with a journal. The scene settings
revision includes every view layer's collection visibility, so a manual
exclude makes a plan stale. An object has a Base, so a visibility to set,
when one of its collections has a layer collection whose own ``exclude`` flag
is clear: exclusion is not inherited from a parent (``layer.cc``). Setting
``exclude`` runs Blender's recursive update: excluding a collection records,
on each descendant, whether it was excluded already
(``LAYER_COLLECTION_PREVIOUSLY_EXCLUDED``, which RNA does not expose), and
including it clears only the descendants without that flag, so a child
excluded before its parent stays excluded when the parent comes back
(``view_layer_exclusion``). A library collection never stands for a local
collection of its name. Staging records the bound view layer's tree with both
flags, read natively (``_angelo_stage.layer_flags``; no 1,024-row cap), and
replays the program's exclusion edits on it in program order with Blender's
rule, skipping an edit that would not change the flag as commit does (writing
an unchanged flag still re-runs the recursion). It refuses any step that would
leave an object this program hides without a Base: the hide itself (an object
already there, moved there, or a new object put there), a later move of that
object, or a later exclude of one of its collections. The refusal is
``VALIDATION`` (``NOT_IN_VIEW_LAYER``), before anything live is written, and
says how to correct the program. Commit applies the exclusion edits in program
order, checks that the live flags are the ones the rule predicts from the
state just before them (a difference is ``CHECK_FAILED``, rolled back), then
the hides (skipping an object the program deletes, whose hide is moot), then
the other collection fields. A rollback writes every layer collection's two
flags back natively (``set_layer_flags``): restoring the edited flag through
RNA would re-run the recursion and change the descendants, the hidden flag
included. A staged ``visibility`` check reads the flag the
program ends with. A collection the artist excludes while the job waits is
stale: the object's own collection changes its revision (``STALE_REVISION``),
and a collection the program moves it into is found at commit
(``STALE_DEPENDENCY``); an object without a Base when the view layer has not
changed since staging stays an internal fault. The ``visibility``
check reads the intents (with each
declared object's recorded state) on a stage and the bound view layer live.
``test_native_visibility.py`` and ``test_blender_visibility.py`` cover it.

Bones
=====

``bone_set`` targets an armature object and names one existing bone exactly
(``action_bones_spec``, ``action_bones``). Rest geometry (``head`` and ``tail``
in armature space, float32-canonical, ``roll``, ``parent`` or ``unparent``,
``connected``) is edited in Edit mode on the private staged armature only,
through the same isolated override ``armature_create`` uses, with the
armature's X-axis mirror editing aid off so only the named bone is written.
Blender's hierarchy rules still apply: a connected bone's head is its parent's
tail (so ``head`` with ``connected: true`` is refused), a connected child's
head follows its parent's tail, and every other bone this moves is reported in
the read-back; a parent cycle or a degenerate bone fails. Bone settings
(``settings``) are written on the armature data and pose values on the
object: ``rotation_mode`` (Blender converts the stored rotation, keeping the
orientation), ``location`` in bone space, ``rotation`` as XYZ Euler written in
the bone's own mode, ``scale`` and ``pose_settings``. Settings follow the
generic rules plus the bone ones: selection is editor state, the transform
channels and rest geometry have typed fields, and no path reaches another bone
(``rna_rules.DENIED_STRUCTS``). A pose bone's custom shape is an object
pointer and a declared dependency. A value an Action, driver or NLA strip
animates is refused, because evaluation would replace the stored value: only
the channels and settings the write touches count
(``action_bones_spec.pose_paths``: a ``rotation_mode`` change that crosses
rotation families touches the new family's channel, which the setter rewrites,
and the old one, which stops deciding the rotation; an Euler order change
touches neither; the pre-approval check follows the mode an earlier
``bone_set`` of the program left), so an unkeyed location of a bone with a
keyed rotation is written and
holds after a frame change, and commit copies only the channels ``bone_set``
wrote (``posed_channels``; every channel after a pose operator). The bone
check reads the same channels, and an NLA strip's Action names the paths it
animates (``bone_state.animated_paths``), so one keyed pose channel does not
refuse every pose write of the bone.

Commit swaps the armature data for rest edits and Bone settings (the
``armature`` family, as for other data) and copies the staged pose for pose
edits (the ``pose`` family, ``bone_state.transfer_pose``): the channels and
writable settings of each bone the program posed (the kernel records them in
the stage's ``extras``), written only where they differ and journaled for
rollback, with the rotation mode journaled after the channels so a
rollback restores it first. Shared armature data follows the shared-data
policy (only the edited object receives the copy). The observed revision's
``bones`` field covers the armature data's settings and every writable Bone
and PoseBone value, B-Bone custom handles included, so a manual edit makes a
plan stale and B-Bone rigs are bindable;
numeric settings are copied a column at a time (``foreach_get``): on a
500-bone rig the field costs about 8 ms and the whole revision about 32 ms
(19 ms without it). Committing any kind on such a rig takes about 1 s, almost
all of it the full program fingerprint and dependency walks that every kind
runs, not ``bone_set`` itself. The derived ``bone`` check states every value an
operation set and verifies it on the stage and live. ``test_native_bones.py`` and
``test_blender_bones.py`` cover them.

Animation edits
===============

``property_keyframe`` sets numeric and boolean ``property_set`` paths of an
object or its data (camera depth of field, curve and text geometry) like
``property_set`` and keys each at one frame, optionally with an interpolation.
Data keys go where Blender puts them: into the object's Action under the
data's slot when the object has one, so that Action always transfers at commit
with the swapped data. A key on a setting drops any earlier ``property`` check
of it, because its stored value then follows the F-Curve; the derived
``keyframes`` check names the path (data paths are read from the data).

``keyframe_delete``, ``keyframe_configure`` (interpolation, easing and handle
types at explicit frames), ``shape_key_add``, ``shape_key_value`` (optionally
keyed) and ``nla_push_down`` edit the program's private copies: keys through
the object's own Action slot channelbag (never ``Action.fcurves``), shape keys
through the staged mesh's Key, whose Action staging also clones. Commit
transfers the staged Action as for ``keyframe_insert``, swaps the mesh (with
its Key) for shape-key edits, and appends the staged NLA track and strip for a
push-down, journaling the created tracks for rollback; with no active Action
left, no slot is assigned. Drivers are refused because their expressions are
not evaluated natively. ``test_native_animation_edits.py`` and
``test_blender_animation_edits.py`` cover them.

These kinds reach any channel of the object's slot, and ``keyframe_edit``,
``fcurve_modifier``, ``keyframe_copy``, ``action_set``, ``nla_edit`` and
``marker_edit`` extend them (``native_coverage.rst``, "Animation"). Their
contract:

* **Ownership.** A kernel edits only the program's private staged copy of an
  Action, and only when the live Action it stands for has no users besides the
  object and its own data: ``anim_stage`` records the live users at staging
  and ``anim_channels.guard`` refuses a shared one (``ACTION_SHARED``, also
  for ``keyframe_delete``). ``action_set`` ``SINGLE_USER`` is the explicit
  isolation; commit reports it (and the split ``keyframe_insert`` or
  ``property_keyframe`` still make) in ``isolated_shared_data``. An object's
  data that shared the replaced Action follows the committed copy, keeping its
  slot, journaled.
* **References.** An existing Action is named by its ``dat:<epoch>:ACTION:<uid>``
  ID, read (never written) to make the stage's private copy, and recorded with
  an exact digest; commit puts the live Action back (as the active Action of
  an ``ASSIGN``, or in each strip that played it) and refuses one that changed
  or disappeared since staging. A program that assigns an Action does not edit
  its keys. Commit renames a copy to the replaced Action's name only when that
  copy stands for it (``anim_commit.replaced_actions``), and carries out a
  ``RENAME`` intent after that, journaled, refusing a name taken meanwhile.
* **Whole-list families.** ``nla_stack`` and ``markers`` replace the live NLA
  stack or marker list with the staged one (strip Actions and marker cameras
  mapped back to live IDs) and read it back; the previous list is a snapshot
  restored by an undo callable that runs before the object journals. Only
  plain action strips are admitted (no meta, transition or sound strips,
  animated influence or time, strip modifiers or tweak mode), so the snapshot
  is complete. Markers belong to the scene: ``marker_edit`` targets the scene
  handle, every bound marker camera must be declared, and the marker list
  joins the scene settings revision.
* **Revisions.** The object revision's animation field holds each key's
  type, every F-Curve modifier's writable settings (Envelope control points
  and Generator coefficients included), the Action's users, slots and manual
  frame range, and each strip's blending, auto blend, reverse, sync, slot and
  selection and each track's lock and selection, so a manual edit of anything
  these kinds write makes a plan stale; a channel with modifiers is not a
  revision limitation.
* **Slots.** Blender picks a slot by the animated ID's name; staged copies
  have private names, so kernels choose the live object's slot themselves
  (``anim_actions.auto_slot``) and commit sets the same slot by identifier.

``test_native_anim_edits.py``, ``test_blender_anim_keys.py``,
``test_blender_anim_structure.py`` and the animation cases of
``test_blender_program_commit_effects.py`` cover them.

Stage isolation
===============

An unlinked Blender datablock is still in Main and can enter an artist's save or
Undo snapshot. Every yield therefore removes private IDs from the artist Main.
For an active job, ``_bpy._angelo_stage`` may transfer a closed, local graph into
a separate native Main. Before transferring anything it verifies the owned ID
set, all outgoing dependencies (including embedded/runtime/library links), and
all incoming links including editor users. Unsupported ID types, physics-owned
runtime caches, interactive edit state or a crossing reference leave the graph
untouched and select file-backed suspension. This does not reduce the supported
program catalog or its limits.

Only the single active writer can retain a native graph, bounded by the existing
stage ID, input and geometry budgets, besides the accepted preview of a chat whose
scene is not shown, which keeps its graph while it waits for the lease (one per
such chat). It retains the existing stage allocations
between polls, rather than an additional clone; the stage's evaluated depsgraph
is freed before transfer. Resume verifies document binding, restores ownership
once, and re-runs the same validity and topology preflight before evaluation.
User counts, RNA identity, topology generations and node authority remain with
the graph. Cancellation, load, Undo/Redo and teardown free the private Main and
invalidate its RNA wrappers. Artist saves and Undo cannot serialize that Main.
The same module reads and writes a view layer's exclusion flags for
visibility programs (``bpy_angelo_stage_layers.cc``): ``layer_flags`` gives
each layer collection's depth, collection name, library flag, session UID and
its ``exclude`` and hidden "previously excluded" bits, depth first (so staging
and commit need no RNA walk of a large tree), and ``set_layer_flags`` writes
only those two bits back, refuses a list whose length does not match the tree,
and tags the view layer for resync as RNA's exclusion update does. Both sync
the view layer first, as every RNA layer access does: a collection linked,
unlinked or removed since the last sync would otherwise be missing from the
read, or read through a dangling layer collection.

Retained previews remain file-backed, so their bounded lifetime/count does not
retain multiple native graphs. Temporary job inspection first materializes an
immutable bundle, then hydrates an independent copy; inspection edits never
alter the authoritative job. Bundle hydration remains main-thread, scoped and
temporary, restoring topology-generation and node budget metadata before
evaluation. File-backed stages are also used when the graph cannot be isolated.

Temporary bundles use an adaptive writer: a prefix of at most 16 MiB stays
uncompressed; crossing that actual serialized size switches to the normal zstd
stream. A failed small raw write retries the retained bytes compressed in the
same temporary file. Close/write failures are propagated before publishing the
bundle. ``STAGE_BUNDLE_COMPRESSION`` keeps compression available, while
``STAGE_BUNDLE_COMPRESSION_MIN_SIZE`` sets the threshold. Normal library writes
and artist project saves do not opt into this policy.

Removal is owned by exact identity: a Main ID is removed only if its captured
pointer, session UID and stable ID family all still match, and each guard
re-scans Main after removal, with an independent batch fallback for survivors. A teardown maps
the captured identities to live IDs once and verifies once; an enclosing guard
skips its own re-scan only when the inner guard proved removal of the same live
set, and still checks any overridden remover. Blocks are removed from their own
ID-type collection for small scenes. Large scenes (at least 1,024 objects or
256 collections) and teardowns of at least 32 IDs use one native batch after
excluding embedded IDs; the same identity guards and failure recovery remain.
``assert_staged`` reads ``Object.users_collection``, which
scans every collection in the file, only for multi-user objects: every
collection link adds a user, so a single-user object linked to the stage
collection has no other link.

RNA subclasses are not stable ownership identities: changing a light's type
refines its Python wrapper from, for example, PointLight to SpotLight. The
kernel refreshes its owned wrapper without adding an ID to the creation ledger;
cleanup and commit use the unchanged ``ID.id_type`` family, pointer and session
UID. Repeated light configurations therefore retain authority, and suspension
or cancellation cannot overlook a copied light after a subtype change.

Exact program fingerprints hash fresh packed RNA bytes and homogeneous numeric
attribute fields, with explicit field/type/shape/count tags and pre-allocation
budgets. Mesh coordinate and topology arrays come from the same contiguous
attributes as scene revisions, and vertex-group weights from the native record
buffer, still charged once per assignment. A program's bind shares two budgets:
2,000,000 recursive RNA items and 256 MB of bytes hashed from single C copies
(packed attributes and native buffers). Packed bytes are not charged against
the item count, which would cap programs near 32,000 vertices, far below the
100,000-vertex input limit. Unsupported layouts retain conservative recursive reads. Custom
properties have depth/work bounds, and every coordinate is still freshly checked
for finite values and allowed bounds. This removes Python-per-element overhead;
it does not replace content checks with pointer, count or depsgraph-only caches.
Exact fences (below) reuse a check only inside one read-only span of code.

Original meshes, object-linked materials, animation and nested graphs are not
edited during staging. Remapping a copied parent preserves its inverse matrix,
local channels and parenting mode through bundle hydration. Only an Object is
remapped as one (``program_fields.repoint``): a Grease Pencil layer also has a
``parent`` and a ``matrix_parent_inverse``, but Blender's only way to set a
layer's parent recomputes that read-only inverse from where the parent is now
(and leaks the bone name it replaces), so no Python route can carry a parented
layer through a data swap. An edit that would swap such data (``operator_run``
on the strokes, or ``property_set`` of a ``data.*`` path, which swaps the
whole data block) is refused at stage validation, before any write
(``layer_parent_swaps``, ``UNSUPPORTED``). Moves and other object-level edits commit.
Lifting the refusal needs a native way to set a layer's parent without
recomputing its inverse. Work/evaluated-geometry budgets are checked before
evaluation and after topology/graph changes. Before commit, the backend checks
fresh exact object/dependency state and repeats declared checks against the
prospective live scene, including unrelated objects for global counts.

Commit transfers only declared changes and their owned dependency closure.
Keyframe insertion transfers its current transform effect as well as its private
action: Euler keys also select the staged rotation mode. Mode is restored before
the fixed transform channels, and transferred current values are checked on the
live object before success. Supported modifier/constraint settings preserve RNA
pointer identities (not numeric arrays) and remap declared staged object links
to their live originals. Mutually exclusive active-modifier and expansion UI
flags are not semantic settings and are not copied. Failed field transfer uses
the same local journal. Staging clones every material and node group of a
written object that the program can write (`Materials a program cannot
write`_) so the stage graph is private; no kernel edits an existing one
(material and node kinds create new IDs), so commit gives committed objects
their live materials and node groups back instead of the clones, found by the
session UID each clone carries (``_angelo_stage_source_uid``, stage-only;
``_restore_source_identities``). Data, Actions and Worlds that commit swaps in
are edited copies, which Blender named with a suffix (``Cube.001``); when the
edited object was the replaced ID's only user, the copy takes its name and the
orphan the suffixed one (``_adopt_names``, journaled for rollback), so an
artist sees ``Cube``, ``CubeAction`` and ``World`` as before. Shared data keeps
its name for its other users and the edited object's copy keeps the suffix. A
first keyframe names its new Action after the live object, not the private
staged copy. These identity steps live in ``program_identities.py``;
``test_blender_commit_names.py`` is the regression test. Results and checks name
what commit will produce, never a private copy: an operation on a declared
object names the object (``program_objects.apply``, not
``__angelo_copy_<id>_Cube``), and a check reads every staged object, material
and node group under the name commit gives it (``VerificationContext.shown_name``,
built by ``program_verification._staged_names``; a guarded material check
under the name its program wrote, its slot proving the identity). Checks and
identity guards never carry a private name: one is up to 24 characters longer
than the name it stands for and would overflow their 64-character name fields
for a material named with more than 40 characters. A copy that commit keeps beside its original takes an exact name
reserved when a result first names it (``stage_names.reserve``: the public
name, else the lowest suffix no local ID and no other reservation holds): an
isolated material (``node_edit`` ``ISOLATE``, and a ``material_assign`` of that
copy), the copy of a shared Action that an NLA push-down plays (its track and
strip take the same name), and a World that other users keep (a fake user,
another scene). The ``world_configure`` result and the delta give the World
the name it takes at commit (``action_world.COMMITTED_NAME``: the live name
when the bound scene is its only user, else its reserved name). When the live
file changes before commit (a reserved name taken meanwhile, a World that
gained or lost a user), commit records the name it actually gave and corrects
the committed result to it (``stage_names.correct``), so the result names
what commit produced in every case. A membership check reads the memberships
staging recorded for each object in program order (``memberships``), and its
observation never names the stage's private collection. Commit links each new
object into exactly those memberships (``program_memberships``), so the
collections a check reads are the ones the object commits into. An Action the
object keeps unedited reads under its live name even when other objects share
it.
The replaced World, like replaced mesh data and a deleted object's data, stays
in the session with no users and is never written when the file is saved;
commit never deletes such artist data. The committed result lists it under
``orphan_data`` (count, at most eight names, and a note saying a save does not
write it, so a model need not spend reviewed scripts deleting it). Data that
only those blocks use, a deleted cube's material, still has a user, so the
next save writes it and it reopens unused (measured in Blender 5.2,
``scenario_hygiene.saved_orphans``). Such materials
are listed apart (``held_only_by_them``, ``program_commit_report.held_only_by``:
a material whose every user is a slot of the unused blocks, local and without
a fake user; one level, so their own images are not followed), and the note
names the call that removes both, ``bpy.data.orphans_purge(do_local_ids=True,
do_linked_ids=False, do_recursive=True)`` in a reviewed script, because typed
programs have no purge: a purge acts on the whole file at commit, outside the
stage, on data other parts of Angelo and the person may hold without users.
Every datablock staging made itself is either kept by the
commit or removed with the stage, so none is left without users.
``test_blender_stage_id_names.py`` covers these on the production path.

Every copy staging makes of a live ID (data, shape keys, Actions, materials,
node groups, particle settings, Worlds) takes a private name
(``__angelo_clone_<stage id>_<name>``, ``stage_names.py``), recorded with the
name of the ID it copies; a copy a kernel makes of it inherits the prefix.
Blender's lowest free suffix ("Wood.001") would take a name the live file
leaves free: a material asked for by that name would be refused
``NAME_TAKEN``, and a datablock made in the stage would skip that number (a
duplicate's mesh "Cube.002" where Blender gives "Cube.001"). Nothing looks a
copy up by name; a staged material, group or World stands for its live ID by
session UID. At commit, after ``_adopt_names``, every staged ID the commit keeps
takes its public name back (Blender adds the lowest free suffix), and an Action
slot or AnimData slot hint named after a private copy takes the public name,
each rename journaled for rollback (``stage_names.publish``); a data-path key's
new slot is "CACamera", never "CACamera.001". Results, check rows and the delta
show public names. Staging also makes one private copy per live material of an
object, however many slots hold it, so a check over all of a material's faces
counts every slot. ``test_blender_stage_id_hazards.py``,
``test_blender_stage_id_review_fixes.py`` and ``test_native_stage_names.py``
cover these.
Failed staging discards its own data. A bounded commit has a local restoration
journal; if restoration or post-commit cleanup fails, the result explicitly
preserves live-change/recovery information and does not auto-undo user history.
Load, Undo/Redo, context changes, expiry and addon teardown invalidate/cancel
staged work. Preview ownership is per conversation and document, with a small
bounded lifetime/count; retained previews are not durable project history.

Materials a program cannot write
--------------------------------

Staging copies each bound object with its data, Actions and modifier node
groups. An APPLY program whose every operation is material-blind
(``stage_borrowing.material_blind``: ``transform``, ``modifier_add`` and
``modifier_configure`` of a ``MODIFIER_SETTINGS`` type, ``modifier_remove``
and ``modifier_move``) and that binds no material identity
(``needs_material_binding``) writes no material, node tree or material slot:
its kernels do not touch them, and commit transfers none of them (no data swap,
no slot or node-tree transfer). Such a program *borrows* each eligible material
instead of cloning it: the staged copy keeps the live material in its slot, so
checks, evaluation and commit read exactly what commit leaves live. A
material is eligible when it, its embedded tree and every nested node group are
local, have no animation data, and link to nothing but each other and images
(which material clones always shared live). Kernels cannot write a borrowed
material either: it is not an owned stage ID
(``StagedActionContext.require_private``).

Nothing the stage copies may be animated either. ``view_layer.update`` makes
the stage's depsgraph active, and an active depsgraph writes evaluated
animation back to the originals. An F-Curve of the staged object, its mesh or
its shape keys whose path runs through a slot into the material
(``material_slots[0].material.roughness``, ``active_material``, a socket of
its tree) resolves through the staged slot to the live material, so the first
stage evaluation would overwrite an artist's unkeyed value before anything was
approved. A program therefore borrows nothing when any ID in a bound object's
mutable closure has an Action, NLA tracks or drivers. The closure
(``program_dependencies._walk_dependencies``) holds everything staging copies:
the object's data of every type (``object_data_types``: mesh, curve, surface
and text, metaball, Grease Pencil, hair curves, point cloud, volume, lattice,
armature, camera, light, light probe, speaker), its shape keys, Actions,
materials, node groups and particle settings, and ``_plan`` names the data and
its shape keys once more, so a type the walk missed cannot make a stage borrow.
A PREVIEW never borrows: its stage is retained, and so privatized anyway
(below). Anything else, and every other program, is cloned: when in doubt,
copy.

The link walks (``_walk_links``) treat an ID that a collection holds, such as a
curve's ``materials``, as a link, exactly like an ID behind a pointer; walking
into it as if it were embedded would take a staged curve or text object into
its borrowed live material's node tree, and stage validation would refuse
every move of one with a material ("still aliases original data").
They also skip per-element views whose items cannot hold a link
(``ELEMENT_VIEWS``: every attribute's values, hair points and curves, point
cloud points, stroke offsets), which a hair or Grease Pencil move would
otherwise visit point by point (``performance.rst``, "Per-edit cost over a
long session").

The bound objects' fingerprints already cover these materials (each is in its
object's mutable closure), so a live edit to one fails verification.
Borrowed references never leave a resident stage. Before every yield,
``stage_borrowing.release`` clears each borrowed slot, so the detached Main or
the bundle stays a closed private graph that holds no live material; hydration
puts the same live material back, found by pointer and session UID, or refuses
with ``STALE_DEPENDENCY`` when it was removed or replaced. Hydration then runs
each material's eligibility walk again and requires the same family it
recorded, so a material that gained animation, an object link or another node
group while the job waited is refused (``STALE_DEPENDENCY``), never evaluated
live. While the stage is resident, stage validation proves that each borrowed
slot still holds its own material (anything else is ``INTERNAL_ERROR``), and
before every stage evaluation no borrowed block may have gained animation data
(``STALE_DEPENDENCY``) and no ID the stage owns may have gained an Action, NLA
tracks or drivers (``INTERNAL_ERROR``: material-blind kernels write neither).
A new stage that owns an animated copy anyway (a data type the walk missed)
unborrows instead of failing: ``finish_stage`` asks ``owns_animated`` before
the stage's first evaluation, when nothing can have been written through a
borrowed slot, and clones each borrowed live material once per object with
``_clone_material``, shared by every slot of the object that holds it, the
copies copy_object makes for a program that borrows nothing. After the first
evaluation an animated owned ID stays ``INTERNAL_ERROR``: an evaluation inside
the stage's own work may already have written the live material.
While resident, a borrowed material counts the staged copy among its users, as
the images that material clones reference always did; no check reads that
count. A retained stage (a job's inspection snapshot) is hydrated, inspected
and accepted outside the job, so before it is written its borrowed materials
are replaced by private clones (``ProgramObjectsMixin.unborrow``): an
inspection edit can never reach a live material. Both paths make one clone
per live material of an object (``stage_borrowing.privatize``), as staging
does, so a check over all of a material's faces reads one material in every
slot; ``test_blender_borrowed_materials_shared_slots.py`` pins it for the
inspection, the ``owns_animated`` unborrow and a DATA and an OBJECT slot
holding the same material. Those clones count against
the stage's ID budget (``MAX_STAGE_IDS``), so staging bounds them up front:
when the copies plus every node a family walk visited could exceed it, staging
clones at once, and an oversized stage is refused while it is created.
On a cube with 32 Principled materials a move copies two IDs into its stage
rather than 66 (object, mesh, 32 materials and their 32 trees), which every
stage validation would walk.

Exact fences
------------

An exact check of an object reads its revision (``scene_fingerprints``), its
custom-link admission and the fingerprint of its whole mutable closure, which
walks every material and node tree it reaches. An edit runs nine such checks
of each written object, and each would walk the closure several times. A *fence*
(``exact_fence``) is a span of main-thread code that only reads Blender data:
no stage evaluation (``view_layer.update`` runs drivers and depsgraph
handlers), kernel, hydration or suspension, ID creation, deletion or write, and
no event processing runs inside it. Nothing in the document can change between
two reads in one fence, so inside it each datablock's dependency links and each
object's closure, fingerprint, custom-link admission and revision are computed
once and serve every check that asks again: a repeated check compares exactly
the content it would have read again. A reused fingerprint charges the
caller's budget what hashing it again would; when that would exhaust the
budget it is hashed again, so it fails exactly where an unfenced check would. A refusal
is never reused. The memo dies with its fence: this is not a cache across
time, and nothing is ever reused because a pointer, a count or a depsgraph flag
looks unchanged.

The fences:

* ``bind`` and ``verify`` each run in one fence, or join the caller's, so the
  fingerprint, the undeclared-dependency search and the evaluation preflight
  walk each closure once.
* A staging job's ``bind`` shares its fence with the stage's borrowing plan;
  staging ends it before it creates its first ID.
* In the commit operator, the fresh precondition, the journal's
  ``commit_started`` record (a file write) and the job's own first
  verification share a fence, which the job's hydration ends.
* Commit's final checks (``ProgramVerificationMixin.final_checks``): the
  stage's last evaluation comes first; then one fence holds stage validation,
  the controller's preparation fence (a fresh precondition of every input), the
  verification against the binding and the written settings' preimage check,
  up to the first live write.

Every callback-capable step (stage evaluation, kernels, hydration and
suspension, stage creation, copying and cleanup, provenance stripping) calls
``exact_fence.end()`` first, so a memo cannot outlive a step that could change
data even if a span were drawn too wide; ``test_native_exact_fence.py`` pins
that list and the commit order. Blender's Python API is single-threaded: like
every exact check, a fence trusts that no other thread writes Blender data
while the main thread holds the interpreter.

A walk inside a fence can still run Python: an add-on property's ``get=``
callback, or a dynamic enum's ``items=``, runs when its value is read. Like
every exact check, a fence trusts that reading a property does not change
data. Blender treats getters as reads (it calls them from panel drawing with
RNA writes refused), and a getter that writes anyway is not caught: the last
walk before the first live write cannot see what its own
getter calls wrote into a block it had already hashed. A fence moves which
getter call that is, not whether one exists. Ending fences at Python-defined
properties instead would end them at nearly every object, since Cycles and
most add-ons register settings on objects and materials, and Python cannot
tell a getter-backed property from a stored one.

Commit evaluates the stage before every check, so no depsgraph handler can run
between the verification against the binding and the first live write, and
the controller's preparation fence follows that evaluation. A backend that does not
declare ``fences_before_apply`` still runs the preparation fence itself, before
commit. Whichever class ``commit`` resolves to across the whole MRO (a
subclass's own override, or a mixin listed before ``ProgramCommitMixin``) must
declare it, in that class or one before it, or the class is refused when it is
defined (``exact_fence.require_declared``). The job hands the fence over as an
``exact_fence.HandOff``, which records that it ran: ``ProgramCommitMixin.commit``
refuses with ``INTERNAL_ERROR`` before its first live write when its final
checks did not run it (an override of ``final_checks``), and a backend's own
commit that returns without running it, or clears it unrun, fails the commit
with ``INTERNAL_ERROR`` and ``partial_changes_possible``. That last check can
only follow such a commit's writes: only the mixin's own write path can be
checked before it writes. When stage validation and the verification would
both refuse, the stage validation's refusal is the one reported.

Within a fence the walkers read each RNA type's property declarations
(identifier, type, read-only flag) once (``exact_fence.table``); outside one,
each walk opens its own. They still read every value of every struct, without
reading the same few dozen types' declarations again at every node and
socket, where most of their time would otherwise go. A type's declarations
change only when Python registers or removes a property, which nothing inside
a fence does. Plain values (strings, numbers, booleans, None) are charged,
bounded and checked exactly as the recursive call would, without the call.

Undo and Redo of a replaced World
---------------------------------

Angelo carries a local fix to Blender's memfile Undo. Without it, after a
commit that replaces the bound scene's World with its edited copy
(``node_edit`` on the World, ``world_configure``), one Undo and one Redo can
leave ``scene.world`` empty: the edited World is read back, with no user. The
fault is in Undo itself, not in staging: a World edit always uses a
file-backed stage, and the stage only creates the new World.

Cause: Blender's memfile Undo, unchanged since Blender 3.6. The edited World
is an ID the commit created, so Undo frees
it. Redo reads the scene, which changed, into a temporary ID and swaps it into
the scene's address (``read_libblock_undo_restore_at_old_address`` in
``readfile.cc``), with a self-remap that points references to the temporary
scene and to its embedded master collection back at the kept addresses. At that
moment the scene's ``world`` pointer still holds the address the World had
when the Undo step was written; ``lib_link`` resolves it afterwards. The read
master collection (528 bytes) is allocated after the World (560 bytes) was
freed, and the allocator can hand it exactly that block: the self-remap then
takes the stale World address for a pointer to the temporary master collection,
rewrites it to the scene's own master collection, and ``lib_link``, finding no
ID written there, clears it. Whether the freed block is still the allocator's
choice depends on every allocation and free in between (Python, timers,
depsgraph threads), so the failure is intermittent. Any ID re-read in place
(scenes, and Worlds, materials and lights with their embedded node trees) whose
pointer names an ID created in that step is exposed; Angelo's commits make it
common because they replace data, Actions, Worlds and trees with edited copies
instead of editing them in place.

Fix. ``ID_REMAP_SKIP_LIB_LINKED_POINTERS`` (``BKE_lib_remap.hh``) limits a
remap to the pointers reading resolves before lib-linking: owner pointers of
embedded IDs (``IDWALK_CB_READFILE_IGNORE``) and non-owning pointers to them
(``IDWALK_CB_EMBEDDED_NOT_OWNING``), the exact set ``lib_link_cb`` skips. The
Undo swap passes it, so pointers still awaiting ``lib_link`` are never compared
with live addresses. Without the coincidence those pointers never matched
anyway, so nothing else changes for the newly read data; in the swapped-out
old data, freed with the old Main right after, node trees' owner caches
(``bNodeRuntime::owner_tree``) are not remapped, and nothing reads them
before the free.

Tests. ``test_blender_undo_unlinked_pointers.py`` (macOS, BACKGROUND) builds the
coincidence deterministically: a scene whose ``world`` holds its own master
collection's address, swapped with ``BKE_lib_id_swap_full`` and the Undo
swap's flags through ``ctypes``; it fails on a binary without the fix.
``test_native_undo_unlinked_pointers.py`` pins that ``readfile.cc`` passes the
flag, that the native test uses the declared flag values and that the remap
filter equals ``lib_link_cb``'s skip set. ``stress_blender_world_redo.py``
repeats the real commit, Undo and Redo in GUI processes with both storages and
allocation churn between the steps (exit status 1 on any failed iteration)::

  python3 tests/python/angelo_agent/stress_blender_world_redo.py \
    --blender /abs/path/to/Angelo --runs 4 --workers 4 --iterations 250

With that command (eight processes of 250 iterations, four at a time) on
full-feature macOS builds of the same source, a build without the fix failed
84 of 2,000 iterations with churn and 1 of 2,000 with ``--no-churn``; a build
with the fix failed none in either mode.

Maintenance. The fix is a local change to Blender's ``readfile.cc``,
``lib_remap.cc`` and ``BKE_lib_remap.hh``, not part of upstream Blender. The
native test's construction is the reproduction: a plain ``bpy`` loop of new
World, Undo and Redo did not hit the coincidence in 2,000 iterations, because
the allocations of a commit matter. When a newer Blender is merged, keep the
flag's bit free of upstream's remap flags, and if upstream fixes the swap
itself, keep the native test and drop the local flag. The native
test needs the executable's exported symbols, so it runs on macOS only; Windows
and Linux builds are covered by the shared C++ and the host pin, not by a run.
If another pointer is empty after Undo or Redo while its target was read back,
suspect the same class of fault: run with ``--log undo --log-level debug`` to
see which IDs were re-read in place, watch the field with a debugger (``lldb``,
``watchpoint set expression -w write``) to find the writer, and read the Undo
step's memfile chunks through ``ctypes`` to compare the written pointer with
its target's written address.

Observation and verification
=============================

``angelo_scene_search``, ``angelo_scene_object``,
``angelo_scene_relationships`` and ``angelo_scene_delta`` provide bounded
read-only intelligence. Cached values are immutable; fresh fingerprints precede
reuse. Deltas reduce response bytes, not the need to prove freshness. Unsupported
or truncated evidence remains uncertain. Object details, relationships and
``coverage="PAGE"`` searches admit object-scoped baselines and fingerprint only
what they return; only a ``SCENE`` search fingerprints the whole scene, and only
a delta against it can report additions or prove the rest of the scene
unchanged. See ``scene_intelligence.rst``.

``angelo_scene_verify`` runs host assertions; ``angelo_expand_recipe`` expands
six deterministic recipes without executing or authorizing them, and
``angelo_apply_recipe`` expands one on the host and stages exactly that
program after the normal approval, in one call. Checks report pass, fail or
inconclusive. The 26-task local matrix exercises actual staged
programs and verifies committed output, including negative controls. It does not
measure model quality or aesthetic judgment. See
``local_verification_and_recipes.rst`` for exact coverage and limits.

``angelo_blender_api`` reads installed public RNA property/function metadata or
BMesh operator documentation, with bounded pagination. Types that a registered
add-on defines (Cycles' ``CyclesRenderSettings``) are found by their RNA
identifier and listed in the type directory: ``bpy.types`` has attributes for
Blender's own structs only. An operator's properties are found in ``RNA`` by its
``bl_idname`` (``mesh.primitive_cube_add``) or its RNA identifier
(``MESH_OT_primitive_cube_add``), C or Python alike, through
``bpy.ops.<module>.<name>.get_rna_type()``, which reads the RNA without polling or
running the operator. ``bpy.types`` has some Python operators and no C ones, so
before this a Python operator registered without a ``bpy.types`` attribute
(``object.shade_smooth_by_angle``, a GPT 6.1 Sol chat on 1.0.6) was "unknown",
like every C operator; all 2,498 installed operators now resolve by both names
(Blender 5.2, factory startup). Every miss is a ``VALIDATION`` refusal that names what was
looked up and the name forms the lookup takes, never ``UNKNOWN``.
``WRITABLE`` describes Blender's own types and names
the owning type for an add-on's. It does not evaluate
expressions, invoke functions, expose instance secrets/paths, or make RNA
metadata a security policy. Current values are a different read:
``angelo_read_data`` reads one bounded data path of the open document (property
names with values, collections a page at a time, enum choices, node socket and
modifier-input identifiers, add-on settings), and ``angelo_blend_file`` reads
the file as a file (datablock counts with unused data, libraries, external
files, unresolved references, or another workspace .blend's datablock names).
Both are read-only, need no approval, never return a file path, and answer a
wrong path or an unreadable file instead of failing
(``scene_intelligence.rst``, "Live data paths" and "The file as a file").
``angelo_agent_capabilities`` returns summaries and
closed family schemas. With ``names``, the ``ACTIONS``, ``CHECKS`` and ``TOOLS``
families return the exact schemas of only those action kinds, check kinds or
tools (``modifier`` names every modifier check, ``modifier:BEVEL`` one), and
``TOOLS`` adds the sculpt or Media Studio workflow guidance; unknown names are
listed, never guessed. ``TOOLS`` without names lists what a Claude plan defers
(``agent_integration.rst``, "Deferred plan schemas"). Results report this
build's runtime features without repeating the action catalog, which only the
``ACTIONS`` family returns (``SUMMARY`` is about 5.7 KB; with the catalog it
would be about 65 KB).
Repeated JSON-schema definitions use deterministic local ``$defs``/``$ref``
without changing runtime validation or permission checks.

Initial observation and change feed
-----------------------------------

Every sent message (and a started voice session) carries a turn context built
on the main thread by ``turn_context.prepare`` and appended as untrusted data.
It keeps the legacy fields (scene, object count, and per object name, type,
handle, location and selection) but orders entries active object first, then
objects the user's message names, then selected objects in view-layer order,
then objects changed since the chat's last turn, then scene order, so the
objects being worked on are never cut off by the 32-entry cap. A scene with more
than 32 objects gets at most eight scene-order rows besides those
(``turn_context.MAX_FILL_OBJECTS``): in a 1,969-object test scene the other
rows were objects no request named (``performance.rst``, "Heavy-scene
payloads"); ``angelo_scene_search`` finds any object. It adds
``active`` per entry (and ``mentioned`` or ``recently_changed`` when true), the
active handle, mode, current frame and selected count, and exact
``object_revision`` values for the active object, up to eight named objects, up
to eight selected objects and up to eight recently changed objects. A model can
bind ``angelo_scene_program`` or ``angelo_scene_verify`` with those pairs
directly, so an edit of any of them needs no discovery query; for Claude that
saves a whole model turn. This grants nothing: binding re-runs the same
``object_revision`` and commit re-verifies revisions and fingerprints, so an
edit made after the observation fails closed rather than being overwritten.
Omitted revisions state ``BUDGET``, ``UNSUPPORTED`` or ``OVER_LIMIT``, and the
``mentions``/``recently_changed`` reports count what their bounds left out.

**Sizes include instances.** An object's own dimensions and bound box leave its
instances out: a Geometry Nodes instancer read 0, a collection instance nothing,
and a curve whose nodes leave only instances its control points padded by their
radius, a bevel scale (a 2 m row of 10 cm cubes read 4 x 2 x 2 m; bounds audit,
2026-10-25). Each component of the evaluated geometry counts its radius where
it draws, the same on every original object type
(``BKE_object_evaluated_curves_radius_is_extent``). Points draw as spheres of
their radius and Grease Pencil strokes that thick, on every object, so both
keep it: a text whose nodes leave one point of radius 2 reads 4 m across.
Curves draw as hair or tubes only as a Curves object's own data, so only there
does their radius count. A legacy curve or a text draws its curves, its own or
ones its nodes make, as a wire (``displist.cc`` sets them as the evaluated
curve's ``curve_eval``). Every other object draws them as an instance of a
temporary legacy curve (``object_dupli.cc`` builds it with
``get_curve_for_render``), a wire of the evaluated positions
(``draw_cache_impl_curve.cc``), which Cycles never renders. A mesh whose nodes
leave a 1 m curve of radius 2 therefore reads 1 x 0 x 0, as the same nodes on
a legacy curve do. Curve to Points on a legacy curve carries the bevel scale
(radius 1) onto the points, which then draw as spheres of radius 1 m and
count so. A radius only widens a box: a negative one never shrinks it past
its points (``test_blender_gn_radius_bounds``). Every reader that states an
object's size (per-turn entries, ``angelo_scene_object``, a script's and a
program's result rows, ``kit.bounds``, a team library import's rows, a
validation ``dimensions`` check, the chosen tools' fit-to-selection box) states
``instances`` (the world ``bounds`` of what they show, and
``own_geometry: false`` when the object shows none itself; ``unknown`` with the
reason when they cannot be read, and then no size of its own) for an object that
makes them, read by a native call (``_bpy._angelo_live.instance_bounds``) that
expands only the object asked about, only when a reader asks about an object
that can make instances, and once until the scene changes (a fact is kept for
the evaluation it was read from, by the depsgraph's update count, which every
evaluation of a change renews: an edit, a frame change, Undo, Redo, a file
load; never served from a depsgraph not evaluated since its last change, and
no change handler that a script could clear): its cost is what that object
makes, never the scene's other instances (about 1.3 ms per 10,000 and 100 ms
for 1,000,000 on Mac 2, test builds, not a timing claim; a scene with no
instancer read pays nothing). A fact is stated for at most 1,000,000 instances
an object (``kit.bounds`` has no limit: a script asked for that box, and it
raises rather than return one short); past it the
fact is ``unknown`` with its reason and ``own_geometry`` (true or false, the one
case it is stated true), and its own size stays only when the read saw its own
geometry. An object showing only instances, and a curve no depsgraph evaluates
(hidden before any update: its data box pads every point by its radius), state
no dimensions or bounds of their own (``angelo_scene_object`` says its GEOMETRY
section is base data it does not show; a ``dimensions`` check of it is refused by
name, with its instance facts); ``angelo_view`` framing adds no origin for
such an object. A part of an object's own geometry drawn as a dupli (a curve's
mesh) is its own, never an instance. Each instance counts as its own box turned
by its matrix, an upper bound: a round instance turned 45 degrees reads up to
about 1.4 times its width. No count is stated: an object's own components drawn
as duplis made counts wrong in ways bounds are not. ``test_blender_object_bounds.py`` checks
every reader against held-out geometry's own truth.

**How an object is hidden is stated too.** An object a reader lists exists
whether or not it is drawn, so a model told only its size could add a second
one beside a hidden original (the maintainer's ruling, 2026-10-25, on an
instancer in a collection hidden by its eye, whose instance facts the readers
keep). The per-turn entries, ``angelo_scene_object``, ``angelo_scene_search``
page rows, a script's ``after`` rows and a program's geometry rows state
``visibility`` for an object something hides, one key per kind, named as
``visibility_set`` names the settings: ``hidden`` (its own eye in the view
layer), ``hide_viewport`` (its own Disable in Viewports), ``hide_render`` (its
own Disable in Renders), and ``collection_hidden``,
``collection_hide_viewport``, ``collection_hide_render`` and
``collection_exclude`` with the names of the collections whose eye, Disable in
Viewports, Disable in Renders or exclusion does (at most four, then a count).
A shown object states none. A collection setting is stated only when it takes
effect as Blender decides it (``layer.cc`` ``layer_collection_sync``): an eye
and the two Disable settings hold for the collections inside the one that
carries them, an object shows through any of its collections free of them, and
exclusion is each layer collection's own; ``collection_exclude`` is stated when
every one of the object's collections is excluded. A program's rows give the
state its commit leaves (its eye and collection edits, its memberships after
commit, its staged Disable flags). ``kit.bounds`` measures the visible objects a
name prefix starts; when it finds none, its error names the hidden ones the
prefix starts with their facts. The facts are read live, never cached with a
scene read's revision (a collection's eye changes no object's revision), by one
helper (``visibility_facts.Pass``) that walks the view layer's collection tree
once per read and only when a listed object sits in a collection, and they are
data only, with no instruction to the model. The instance pass itself is
unchanged: an instancer hidden by a collection's eye or by Disable in Renders
keeps its instance facts; one hidden by its own eye, or disabled in viewports
by itself or a collection, gets none (Blender gives such an object no visible
instances: ``BKE_object_visibility``). ``test_native_visibility_facts.py`` pins
the rule.

An ``OVER_LIMIT`` mesh's omission gives each count over the program input limits
with the limit (``counts``), ``route: angelo_execute_python`` and a ``note``
that only typed programs have these limits, that the mesh can stay as it is and
that angelo_execute_python edits a mesh of any size
(``action_schema_types.OVER_LIMIT_NOTE``); binding it anyway is refused with the
same counts and that note (``LIMIT_EXCEEDED``), and a program that leaves its
binding to inference is told its revision was omitted, with the note, not to
look it up. Told only that a mesh exceeds the program limits, or given only
the counts and the route, a model tends to cut its own mesh to a guessed size
to fit; the note says it need not. The
encoded payload is bounded to 16,384 characters by dropping rows that are not
selected, named or recently changed, then feed, then the remaining non-active
rows; the active entry always remains.

A named object is a scene object whose exact name occurs literally in the
user's own message: case-sensitive, not adjoined by a word character, and not
inside an occurrence of a longer named object ("Cube" inside "Eval Cube" is not
a second mention). Names are untrusted data: a match selects a bounded
observation row and authorizes nothing. At most 20,000 names and 64 candidate
names are considered; a name shared by two objects (for example a local and a
linked one) is reported as ambiguous instead of guessed. Recently changed
objects are the change feed's most recent rows, which include the chat's own
commits, so an object the agent created or edited in an earlier turn arrives
with a fresh revision even when it is neither active nor selected. A row's name
only locates a candidate: its session UID must match, so an object that took a
removed object's name is never revised in its place. A human UI Accept of a
preview, which has no model waiting for its result, reaches the next turn the
same way. Voice sessions have no message text, so they add no named rows.

``changes_since_last_turn`` reports what the change feed recorded since the
chat's previous delivered turn context: changed object handles with their
change kinds, a shading-only count, changed datablocks, overflow flags, and
selection/active/mode/frame/object-count differences. Load, Undo/Redo and ID
replacement report only that the document epoch changed. The feed is reset
when the send succeeds (a planning boundary); a failed send keeps it. The
source of a change is unknown and is labelled so: it includes this chat's own
committed programs from its previous turn, other chats and manual edits. The
labelled hints never replace re-observation or the commit checks.

Claude receives this observation as its initial prompt observation. A
continuation round (``verify=true``) re-sends the plan's results with the same
bounded builder, run on the main thread by the controller's tool pump for the
original message's text and without acknowledging the change feed, so the
user's next message still reports the turn's changes. The results already carry
exact read-backs and post-commit revisions; the observation adds the scene
state and the objects changed during the turn. A plan that is a cube/transform
batch still re-sends the full ``angelo_inspect_scene`` read-back (up to 200
objects with locations), the evidence that verifying a batch of up to 64
created or moved objects relies on. Read-only ``angelo_delegate`` consultations
also keep their existing 64-object observation. ``ANGELO_TURN_CONTEXT=legacy``
restores the previous observation (the first 32 objects of
``angelo_inspect_scene``) for the initial prompt and continuation rounds, and
stops change recording. Cost is measured in ``performance.rst``;
``test_native_turn_context.py``, ``test_native_round_savings.py`` and
``test_blender_turn_context.py`` pin these contracts.

Visual observation
==================

``angelo_view`` is a read-only collaborator tool for appearance that structured
data cannot prove: silhouettes, proportions, toon shading, material look,
lighting and the rendered picture. ``view_spec`` owns the contract,
``view_capture`` the main-thread capture, ``view_framing`` the camera math,
``view_cache`` the bounded stores and ``view_png`` the encoder;
``view_render``, ``view_finished``, ``view_job_output`` and ``view_picture``
own the rendered pictures (`Rendered looks`_). Arguments are up to 16 observed
object handles (none means all visible geometry), 1–3 views (or up to six
reference pairs, `Free cameras and reference pictures`_), ``SOLID``,
``WIREFRAME``, ``MATCAP``, ``CURVATURE``, ``MATERIAL``, ``ATTRIBUTE``,
``WEIGHT``, ``LIT`` or ``RENDERED`` shading (`Sculpt looks`_), a 256–1024
pixel resolution and, with RENDERED only, ``region``, ``image``, ``frame`` and
``file``. Pictures beyond the viewport (an Image
data-block's pixels, a UV layout, vertex weights and colour attributes, what
the person sees, render passes and strips over ``frames``) have their own
contract in `view_sources.rst <view_sources.rst>`_. A capture of the person's
editors can include file paths drawn there and is announced to them in the
chat. Each view is a
named axis (``FRONT`` looks along +Y; ``BACK``,
``LEFT``, ``RIGHT``, ``TOP``, ``BOTTOM``, ``ISO``) or ``FIT`` (the view
direction of the bound window's first 3D Viewport), both orthographic and
fitted to the targets' visible bounds with margin; ``SCENE_CAMERA`` (the active
camera at the render aspect); an earlier ``view_id``, whose exact camera is
reused for before/after comparison at any shading or resolution; or a free
camera given by its location and the point it looks at (`Free cameras and
reference pictures`_).

Each result reports the document epoch, every target's handle, visibility and
production ``object_revision`` (or ``revision_exact: false`` with its reason),
each view's camera (world and projection matrices, orthographic scale or lens,
clip range), size, capture time, timings and explicit ``uncertainty`` notes.
PNGs go to the chat's private reference directory and through the existing
image path: Codex receives ``inputImage`` items, and Claude receives them in its
next verification round, sharing the request's view budget and the
refresh-after-mutation rule of sculpt views.

**The view budget of a Claude request.** Every view a typed plan asks for is
captured again and attached after each later edit, so their number is bounded
(``claude_evidence``): twelve distinct images per request
(``VIEW_IMAGE_LIMIT``; another angle, target, shading, resolution, framing or
region is another image), of which at most six are sculpt views or reviews
(``CLAUDE_SCULPT_IMAGE_LIMIT``), because those also hold a native camera each
and a baseline per review in a twelve-camera store. A plan that asks for more
is not run and does not end the turn: it goes back to the model as a plan
problem that says how many images the request keeps, how many are left and
how many the plan asked for, like any other rejected plan
(``ClaudeVisualEvidence.problem``, read by ``plan_validation.screen_plan``),
within the request's budget of plan corrections
(``plan_recovery.CLAUDE_PLAN_REJECTIONS``). RENDERED looks and finished
pictures are delivered once and are not counted.

**A view with invalid arguments never blocks its plan.** ``angelo_view`` is a
lookup, so a Claude plan whose only invalid operations are views runs the rest
(``agent_integration.rst``, "Operations the host refused alone"). Before it is refused,
a view request that has exactly one meaning is normalised
(``view_spec.normalize_view``), and its result says so (``normalized``, with
``results`` holding each call the host made). Two classes, each validated
whole afterwards, so the request's only fault was the one fixed:

* RENDERED with ``SCENE_CAMERA`` and named cameras (axes, ``FIT``) becomes
  RENDERED for ``SCENE_CAMERA`` plus LIT for the others: RENDERED renders only the scene
  camera, and another camera's look with the scene's lights and world is LIT.
* An existing picture (``image``) with one views item and shading left out or
  RENDERED is shown as RENDERED ``SCENE_CAMERA``, the only form the contract
  has for it: no camera or shading changes a picture.

These were the model's own corrections in 13 of 14 and 2 of 2 recorded
refusals of these two kinds (the 14th asked for the render alone).
Left alone and refused, with the validator's fix hint: an earlier view_id with
RENDERED, beside ``SCENE_CAMERA`` or not (it may mean the scene camera's
render or a LIT look of that exact camera, which may have moved since),
``frame`` without a job picture (a look shows the current frame, so dropping
it would show another frame than the one asked for), an explicit viewport shading beside ``image``,
and everything else. The two calls of a split view are independent reads: the
LIT look is drawn even when the render fails, and only a stop or a document
change ends the slot early (the note then says how many calls were made). A
normalised view that would add kept views beyond the view budget is refused
instead when the plan fits without them, never costing the plan its run, and its refusal says the budget would
refuse its LIT form too. A plan stops at a refused view: nothing after it
runs.

**The refresh after an edit is bounded in time.** The captures draw on
Blender's main thread, and on a heavy scene one view takes seconds, so twelve
kept views could block the interface for most of a minute after every edit.
One edit spends at most five seconds on them
(``claude_evidence.REFRESH_SECONDS``; ``ClaudeVisualEvidence.refresh``). The
view the model asked for most recently is always captured; each further one,
in that order, only if the time left covers what a view costs, taken as the
larger of what this refresh has measured and what the view's own last capture
reported (``timing.capture_wall_ms``). On a light scene every kept view is
captured in the grouped calls. Views that were left out are *stale*:
they are never attached, the next round's result lists them as the model
would ask for them (``stale_views``, reason ``refresh_budget``) with one
sentence on what to do, and the model asks for one again, with the same
arguments, when it needs it; that is not a new view of the budget. A kept
view is captured again only after an edit, never because it is still stale.

**A capture that fails does not end the turn.** A view the model asked for
that cannot be captured (no 3D Viewport, a modal interaction, no camera for a
look), or one part of a normalised view that fails, is an ordinary failed lookup: the model reads the error with the plan's
other results in the same turn (``agent_integration.rst``, "Failed plan
steps"). A kept view whose capture fails during the refresh is listed in
``stale_views`` with reason ``capture_failed`` and its error; the edits are
not repeated and the other views are still captured. Only a capture that the
person or the host decided (Stop, a document change) ends the request, as it
does for any tool. In a Codex thread a failed capture is a failed tool output
inside the turn. The cost of a kept view is its capture after an edit and its
image in every later round (``performance.rst``, "Rendered looks").

The tool never changes scene data, selection, active object, mode or frame.
``draw_view3d`` reads shading from a SpaceView3D, so one 3D Viewport's shading,
overlay and clip-plane settings are set for the call only and restored in
reverse order in ``finally``, with no event processing or redraw in between;
equal values are not written. A viewport that is not in Rendered shading, local
view, local collections or camera view is preferred. Leaving Rendered shading
restarts that viewport's render engine, which the result reports. No Blender
Image, temporary file, Undo step or Main ID is created: pixels come from a
direct GPU texture read and a standard-library PNG writer encodes them on worker
threads that never touch ``bpy``, overlapping the next view's draw.

Fitted views cut away geometry nearer than the targets' bounds; other visible
objects still appear and can occlude. Isolating targets would require hiding
objects or local view, both scene or viewport mutations, so it is not offered.
SOLID and WIREFRAME use fixed Workbench settings. MATERIAL is EEVEE Material
Preview with the built-in ``forest.exr`` HDRI, without scene lights or world,
and is not a final render. ``draw_view3d`` renders it as a viewport image
render, in which EEVEE compiles material shaders synchronously and renders the
scene's viewport sample count, so no deferred-compilation placeholder is
presented. So that an agent's view never compiles them on Blender's main loop (a
view after new materials blocked it 446-652 ms in the parallel benchmark, about
4 s on a cold launch), the agent's MATERIAL and LIT views first queue the
shaders the scene still lacks to Blender's background compilation
(``view_warm.py``: a small private EEVEE draw as an XR-surface offscreen, the
one offscreen mode with deferred compilation, its pixels unused;
``_angelo_live.shader_state`` reads which of the scene's materials have no GPU
material yet or one still queued) and the tool call stays pending, Blender
running, until they are compiled, then draws; in a session whose agent draws
such views, a merge queues its parts' shaders at once. A view the approval mode
refuses (Read-only, Ask, a sculpt session the draw would update) neither waits
nor draws first: it is answered at once, from the view cache or with the
refusal. Other callers (a flow's Scene Capture, a library thumbnail) draw at
once as before. Frame strips check their own frame-change, playback and job
admission before shader preparation too; safe display writes alone do not admit
a strip's frame changes. Callers of the executor itself must poll a pending answer as
the controller does (the GUI suites settle it through the event loop). The first draw of
newly compiled shaders builds Metal pipeline states on the main thread (Blender
sets no parent shader for materials, so its pipeline warming never runs): about
80 ms a new shader and vertex format on a cold cache. Before the view, the
pending call therefore draws, on private offscreens, every object that draws a
newly compiled material and then every instancer, none assumed to share
another's pipeline (separated parts and meshes with other attributes build their
own); how many share a draw doubles while draws cost about what a draw of
nothing costs and drops to one after one that built something, and a tick draws
until about 30 ms of it are spent. The queue draws, an object a draw within the
same tick budget, the fewest objects that draw every missing material. A merge's
materials get these draws once compiled, a later merge adds its own, and a view
asked before they finish completes them first. First draws are kept per look
(LIT's scene lights and world build other pipelines than MATERIAL's studio
light): a view in the other look adopts a merge's still owed work and draws its
first draws again, spread, before it is drawn; once a merge's spreading or a
view has finished, nothing is owed to the other look any more (Residue below).
The worst main-loop gap of such a view measured 109-129 ms against 1.65-1.75 s
before (Mac 1, 8 new materials a step, the round-4 build 0291af53bee). The
warming draws use the look asked for: LIT with the scene's lights and world,
MATERIAL with neither, so a MATERIAL view never compiles a world it does not
show. The view is drawn only once nothing its scene draws is missing or queued,
whatever arrived during the wait (drawing it would compile a missing shader on
the main loop and wait for a queued one without a bound); after 60 s with
anything not ready or owed its first draw, or a state that cannot be read, it is
not drawn and answers that its shaders are not ready, and the agent asks again;
denied again with nothing changed since, it is drawn in Solid instead, labelled
so (``shaders_not_ready`` names what is not ready), so a material that never
compiles can never deny every image. Materials are told apart by datablock
(session UID), never by name. A material whose compile failed is drawn with
Blender's error shading, and a failed LIT world with Blender's default world, as
in the viewport; the view, a strip frame by frame, names them
(``shaders_failed``). Where the view still compiles on the main loop as before:
a new variant of a material already compiled for other geometry (hair curves
after a mesh), the other frames of a strip, and an object with many new
materials, whose one draw builds them all. World shaders are compiled
synchronously by EEVEE in any draw. If shader compilation is still pending
afterwards, the view is marked ``shaders_compiling`` and not cached. Camera view
may let EEVEE apply that camera's depth of field or render border, which is
reported. Entering MATERIAL re-tags objects in Sculpt Mode for a geometry
update, as any switch to Material Preview does.

**Residue (1.1).** Under the maintainer's ruling for 1.0.6 C3 (burst only: shaders
already compiled in the background, Metal pipeline setup only, no wrong pixels,
no unbounded wait, never worse than drawing without the wait), these paths still
build pipelines in one main-loop block and are 1.1 work:

* One object or one instancer with many new materials: its single first draw
  builds all of their pipelines (about 330-360 ms for five materials on an
  object, Mac 1, against about 4.1 s without the wait).
* A new variant of a material already compiled for other geometry or passes
  (a mesh material reused on hair curves; new probe, volume or shadow passes):
  not seen as missing, so the view compiles and builds it as before.
* The frames of a strip other than the current one: prepared by none of this;
  each tile's draw compiles and builds what it needs.
* A new LIT world: EEVEE compiles world shaders synchronously in any draw.
* Instancers, whose instances' materials are unknown: up to ``GROUP_MAX`` (4)
  share a first draw while draws stay quick, so their new pipelines can build
  together.
* Batching by ``(material, format)``: the format is read from the original
  mesh (UV map count, colour attribute names), so evaluated formats (Geometry
  Nodes) that differ can build several pipelines in one batched draw.
* Materials that became compiled without ever being pending to a view or a
  merge (compiled by the artist's own viewport, say): no view owes them first
  draws, so the view builds their pipelines.
* The session rail's Main picture can run two of its redraw's stages in one
  main-loop turn when a worker's save reaches the rail in the same turn.
* The other look after a finished preparation: once a merge's spreading or a
  view has drawn its first draws, nothing is owed to the other look, so a
  later view in that look builds those pipelines in its draw (VW12-L-1).
* At the deadline, owed objects are listed again only when no list was made;
  an emptied list with a material compiled since is not listed again, so the
  view builds that material's pipelines in its draw (VW12-L-2).
* A merge whose idle wait ends after its window switched scenes stores the
  other scene's materials as owed to the scene it began with, so they are
  drawn first only when that scene's view asks, and a view of the other
  scene builds them in its draw (BREAKIT-lt-pa LT-M-2).
* Owed work is stored under a key that includes the scene's and view layer's
  names, so after a rename a denied or stopped view's owed first draws are not
  adopted by the next view of the same layer, which builds them in its draw
  (BREAKIT-lt-pa2 LT2-M-1).

Identity residue (wrong target, never freed memory; needs native lifetime
identifiers, 1.1): a view layer deleted and replaced at the same address
between two polls, like a window replaced at its address on another workspace
(``live_target``), can pass the wait's checks, so the view would capture the
replacement's visibility (BREAKIT-lt-pa2 LT2-M-2, BREAKIT-lt-pa LT-M-3).

``LIT`` is the MATERIAL draw with the viewport's scene lights and scene world
turned on instead of the HDRI: EEVEE with the scene's own lights, and its world
as light and background, from any view. MATERIAL cannot show lighting, a sky or
a world volume, so judging those would need a render. LIT costs what MATERIAL costs
and is drawn the same way, on the main thread. It shows viewport data (viewport
modifier levels and visibility, no compositing), and for a scene whose engine is
not EEVEE it is an approximation, which the result states
(``lit_preview.approximation`` and the note). A LIT image is never cached: the
scene's colour management and film settings change it without a depsgraph
update. A viewport read is opaque, so a scene whose film is transparent is
drawn on black; the result says so (``lit_preview.transparent_film`` and the
note) and names RENDERED, which shows the transparency.

MATERIAL and LIT never draw motion blur (``view_blur.py``). When the scene
renders with motion blur (``render.use_motion_blur`` and the view layer's
``use_motion_blur``), EEVEE blurs an image render of the viewport by the motion
since the previous draw on the same offscreen. With one offscreen per picture
size, each view after the first would be smeared toward the previous view's
camera, and each strip tile toward the frame before it. While blur is on, each MATERIAL or LIT
draw gets a new offscreen, which has no earlier draw and so no velocity; it is
freed before the next, so one EEVEE instance exists at a time. Workbench looks
and scenes without blur keep sharing one. No setting is changed. The result
says the blur is left out (``material_preview.motion_blur`` or
``lit_preview.motion_blur``, or a strip view's ``motion_blur``:
``NOT_DRAWN``), so a sharp viewport look is not mistaken for the render. The
cost of a new offscreen per view is unmeasured; it is paid only while blur is on.

A six-image, 24 MB LRU cache coalesces identical requests. Its key holds the
document epoch, scene, view layer, frame, drawing-viewport filters, target
handles and exact revisions, camera matrices, shading and size. Targets without
an exact revision and uncertain views are never cached. Any depsgraph update of
the scene drops its images; load, Undo/Redo and unregister clear images and the
32 most recent REUSE cameras, so a ``view_id`` expires with the document epoch,
like scene handles. View cameras never authorize edits or sculpt targeting.

Free cameras and reference pictures
-----------------------------------

A ``views`` item may be a free camera, ``{"location": [x, y, z], "look_at":
[x, y, z]}`` with ``lens`` (millimetres, 1–5000, default 50) or ``ortho_scale``
(world units, 0.001–100,000), never both. Without it a model could see the
scene only from the fixed axes, its own viewport or the scene camera, so
looking from a reference picture's viewpoint would mean creating or moving a
camera object, an edit, or a script, or skipping the comparison.

**Read-only by construction.** A free camera is matrices, drawn by the same
``draw_view3d`` path as an axis view (``view_framing.free_camera``,
``view_free_camera``): no camera object, data-block, Undo step or setting is
made, and the scene camera, selection, frame and viewports are untouched. Its
convention is the one ``camera_create``'s ``look_at`` makes
(``action_camera_light_spec.look_at_euler``): no roll, up toward world +Z;
looking straight down or up, world +X is the picture's right, as in the TOP
and BOTTOM views. The lens is on Blender's default 36 mm sensor, and the sensor
or ``ortho_scale`` spans the picture's longer side (sensor fit Auto). The
picture has the scene's render aspect, as ``SCENE_CAMERA`` has, and equals the
one a camera object with these values and the reported clip planes makes:
``test_blender_view_camera`` compares them pixel by pixel. With a ground or
room around the camera in a scene 100 to 500 units deep, the reported planes
are Blender's own 0.1–1000, so the picture equals a new camera object's.

**Clip planes from the visible scene, never from look_at.** ``look_at`` only
aims: its distance says nothing about where geometry is. Depth along the
viewing direction is linear, so the corners of every visible drawn bounding box
(the scan of the fitted views, ``view_capture._framing_points``) bound the
depth of everything drawn (``view_framing.box_depths``). A perspective depth
buffer resolves depth z to about z²/(clip_start · 2²⁴): precision is set by
clip_start alone, so clip_end is generous, at least twice the farthest box and
ten thousand times clip_start (Workbench's shadows and cavity need it well past
their casters: a far plane just past the geometry drew an eye-level SOLID view
23 levels darker), and clip_start is chosen between keeping near geometry and
resolving far geometry (``view_framing.free_clip``):

* only boxes the frame can show count: a box wholly outside one of the
  frame's four side planes (a pebble beside the camera) sets neither the near
  plane nor a note, and particle hair widens its emitter's box by its longest
  hair length and a quarter;
* when no box reaches the camera's plane, half the nearest box's depth, since
  nothing is nearer;
* when a box reaches it (a ground, a valley, a room, a sky dome around the
  camera) its geometry may be anywhere in front, so Blender's 0.1, or a
  thousandth of the scene's depth for a scene under 100 units deep, or half the
  depth of a nearer box wholly in front;
* never nearer than the scene's depth divided by 100,000 (Blender's viewport
  default range, 0.01–1000), so the farthest geometry stays distinct.

So a near subject is kept unless the scene's depth is more than 100,000 times
its distance (a 20 km ground and a cube 0.6 ahead keep the cube). An
orthographic camera starts at its own plane and ends at twice the farthest box
or 1,000, whichever is farther. With nothing visible in front, the range is
Blender's 0.1–1000. The result states ``clip_start`` and ``clip_end``, and says
when geometry may be cut: every visible box behind the camera
(``camera_faces_no_geometry``), a box wholly in front nearer than clip_start
(``camera_clip_cuts_near_geometry``), or bounds read only in part, by the
instance or the strip's time budget (``camera_clip_bounds_truncated``; the far
reach then doubles). Geometry of a box that reaches the camera's plane and lies
nearer than clip_start is not drawn, as for any camera. A strip holds a free
camera over its frames, clipped to every visible box at every frame shown, and
states the same notes.

**REUSE clips the scene as it is now.** A free camera's ``view_id`` keeps its
location, look_at, lens or scale and aspect (``free`` in the stored camera)
and its clip planes are computed again from the scene at each REUSE, so an
object added nearer than the old clip_start appears in the "after" picture of
a before/after comparison; a strip's REUSE does the same over its frames. The
view matrices are unchanged, so framing compares exactly.

**Reference pairs.** A free camera with ``reference``, the ``reference_id`` of
an image attached to the conversation (listed with the message that attached
it), returns one picture: that image on the left, a two-pixel gap, and the view
on the right, drawn at the image's aspect, each ``resolution`` pixels on its
longer side (``view_compare``). A camera at the image's viewpoint therefore
frames the scene as the image does; matching viewpoints give matching halves.
The image is found in the conversation's private folder by its id
(``ReferenceStore.attached_image``: a canonical UUID and a regular PNG, JPEG or
WebP of at most the attachment limit, never a link or a path of the model's
making); an earlier view's ``view_id`` names a picture in the same folder and
works too, which shows an earlier picture beside the scene now. The size is
read from the header before anything is decoded (``view_picture.header``,
``view_compare.webp_size``, and palette or grey PNGs of 1–4 bits), and the
image is decoded and scaled by ``imbuf`` outside Main, as ``view_picture``
does; transparency is shown over a grey checkerboard and stated. A JPEG's EXIF
orientation, which ``imbuf`` does not apply, is applied (``exif_orientation``
in the record), so a phone photo stored sideways is shown and matched upright;
orientation in PNG or WebP metadata is not read. An image more than eight times
wider than tall, or taller than wide, is refused, as are an unknown id, a
non-image attachment and a file that does not decode: each a ``VALIDATION``
refusal naming ``views[i].reference`` and never the private path. The record
states the layout (``REFERENCE_LEFT_VIEW_RIGHT``), the picture's and the view's
sizes and the reference's id and displayed size. A pair's ``view_id`` is a
REUSE camera at the image's aspect. Pairs are never cached: the picture holds
the reference besides the view. An image several pairs of one call share is
decoded once for each size it is shown at.

**Over the view, or as outlines.** ``compare`` (with ``reference``) shows the
image another way. ``PAIR`` is the side-by-side picture above and the default.
``OVERLAY`` is one picture of the view's size: the image at half opacity over
the view (layout ``REFERENCE_OVER_VIEW``), so an offset between them is judged
within one picture rather than across two. ``OUTLINE`` needs ``targets`` and is
the view with two outlines drawn on it (layout ``OUTLINES_ON_VIEW``): the
image's subject in orange and the targets' silhouette at the same camera in
cyan, each pixel only one of them covers tinted its colour. Two pictures side
by side at 512 pixels leave a 2 mm likeness judgment (5 pixels at the face and
hair tests' framing, `oneshot_tests.rst <oneshot_tests.rst>`_, "Grader
uncertainty") to the model's eye; the record's ``outline`` measures it. A
``PAIR`` or ``OVERLAY`` that names ``targets`` carries the same ``outline``
record beside its picture (nothing more is drawn on it), so the measurement
reaches the model whichever ``compare`` it asks for; models compare by pairs
or overlays far more often than they ask for ``OUTLINE`` (`performance.rst
<performance.rst>`_, "Reference comparisons"). Without ``targets`` nothing is
measured.

* both outlines are made alike (``view_outline.normalized``), so the same
  shape compares as the same whatever its topology: gaps and slots narrower
  than twice ``closed_px`` (3 pixels at 1,024, in proportion at other sizes)
  are joined, so strands read as one mass, parts smaller than 0.1% of the
  picture are left out, and holes are filled, except against a cut-out
  (``reference.holes``: ``COMPARED`` there, ``FILLED`` otherwise), whose
  transparency can show a hole where a colour picture cannot tell a hole from
  background;
* ``mean_px``, ``p95_px`` and ``max_px``: every edge pixel's distance to the
  other outline's nearest, both ways, and in world units (``*_world``) from
  ``pixel_world``, one pixel's size at the median depth of the targets' own
  outline (``pixel_world_at``: ``TARGETS_OUTLINE``; ``LOOK_AT`` without
  depth), the scale the gaps use too. Outlines of more than 8,000 edge pixels
  (dense hair or foliage at 1,024) are compared at half size or less, which
  ``measured_every_px`` states; distances are worked out a bounded chunk at a
  time;
* ``iou``, their overlap; ``centre_offset_px``, the targets' centre from the
  subject's, right and down; ``size_ratio``, the square root of their areas'
  ratio;
* ``gaps``: up to six places where the outlines are farthest apart (two
  pixels or more), largest first, searched from both outlines and spread along
  them (among equal gaps the image's outline comes before the targets', each in
  image order, top row then left, so the same pictures list the same places on
  every machine): the
  targets' edge pixel there and the 3D point it shows (``surface_world``,
  from the silhouette's own depth), the offset in the view's plane from that
  point to the image's outline (``offset_world``, world units at that point's
  depth), and ``OUT`` where the image's subject reaches past the targets or
  ``IN`` where they reach past it (a part only the targets have, such as a
  horn or a prop, is ``IN``). A world-space brush path can start at
  ``surface_world`` and follow ``offset_world``; the picture shows the same
  places tinted;
* ``reference.subject_from``: ``TRANSPARENCY`` for a cut-out (its alpha is the
  subject), else ``PLAIN_BACKGROUND`` (the median of the image's border is the
  background and every pixel more than three levels a channel from it, or
  three times the border's own spread, is the subject's:
  ``view_outline.subject_mask``), ``BUSY_BACKGROUND`` (a border of many
  colours, ``reference_background_busy``) or ``AMBIGUOUS_BACKGROUND``: a
  second colour covers a fifth of the border or more, so the majority colour
  taken as background may be the subject's own, as with a shirt filling the
  frame's bottom (``reference_background_ambiguous``, which says a cut-out is
  outlined by its transparency). A subject cut by the image's edge is stated
  too (``reference_subject_cropped``).

The silhouette is drawn by the host on the GPU (``view_silhouette``): what the
depsgraph draws for the targets (each one's own evaluated geometry and every
instance it makes: Geometry Nodes instances, instanced collections, particle
instances, each at its own matrix), alone and flat, with the view's own
matrices, so it is the targets' outline whatever stands in front of them. It
is drawn at twice the view's size and a pixel any of its four samples covers
is the targets', the way an antialiased picture shows a pixel the subject only
touches. Meshes, and curves, surfaces, text and metaballs as Blender evaluates
them into a mesh, give their triangles; hair curves (also a node tree's) give
each curve as a line as wide as its points' median radius, and particle hair
(parents and children, up to 200,000 points, more stated) at its strands' mean
radius; a curves object with no curves draws nothing. Targets that draw
nothing are counted (``not_drawn``, ``outline_targets_not_drawn``). The
triangle and segment bounds are checked before any geometry is copied, and the
GPU depth state is put back as it was. The outline and the measures are worked
out on the PNG worker thread with NumPy alone (``view_outline``: connected
regions by runs, no SciPy). ``test_blender_view_outline`` checks them against
Blender: a head drawn through a free camera matches itself within a pixel, a
known move gives the centre offset ``world_to_camera_view`` gives and a known
scale its ratio, every gap's ``surface_world`` lies on the moved surface
(Blender's ``closest_point_on_mesh``) and its offset points back along the move
as Blender projects it, and a hair object's silhouette holds every strand point
Blender projects. ``test_blender_view_outline_shapes`` compares every kind of
shape with Blender's own picture of it (a torus through its hole, by colour and
as a cut-out, Geometry Nodes instances, dense, sparse and particle hair, a mesh
beside hair): within a pixel at the 95th percentile each, with stray strand
tips of sparse hair up to about 10 pixels apart at most. Legacy particle hair is
less exact: Workbench draws its strands at its own width, and over six seeds
its outline matched within 1.0 to 3.6 pixels at the 95th percentile, stray
strands up to 40 pixels apart.

**Deliverable pictures.** A side-by-side pair is twice a view's width, so its
halves are scaled down, aspect kept, until the picture is at most 2,000 pixels
wide and 1.6 million pixels (``view_compare.fitted``; ``view_size`` states the
halves): Claude's limit on an image's side when a request holds many images,
and an RGB PNG under the conversation's 5 MiB image limit even when nothing
compresses. At 1,024 a square pair is 1,788 by 893; at 512 nothing changes.
An ``OVERLAY`` or ``OUTLINE`` picture has the view's own size.

**Limits.** A call has 1–3 views, or up to six free cameras that all have
``reference`` (``view_spec.MAX_PAIRS``); each pair is one picture of a Claude
request's twelve (`Visual observation`_), and after an edit kept pairs are
captured again in calls of at most three, as other views are. A free camera
works with every viewport shading (``WEIGHT`` included) and with ``frames``;
``reference`` does not combine with ``frames``, and ``RENDERED`` keeps
rendering the scene camera alone. MATERIAL and LIT draws keep the fresh
offscreen per draw while the scene renders with motion blur. Every number is
an int or a finite float in its range; an integer past a float's range is a
``VALIDATION`` refusal for every tool (``tool_specs.validate_call``), never an
``OverflowError``.

**Cost.** Six LIT pairs at 512 pixels took 0.33–0.42 s on the five-object
fixture of ``test_blender_view_camera`` (M-series Mac) with small references,
0.33 s for six pairs of one 12-megapixel JPEG, and 0.017 s for three free
cameras without references. A pair's
PNG is about 125 KB there. Decoding runs on the main thread before drawing; the
composition and encoding run on the PNG worker threads. One call per
event-loop tick, as tool calls run, seventy-five six-pair LIT calls at 1024
grew the process by 0.5 to 16 MB from call 30 to 75 in seven runs, allocators
reaching their working size (three-view LIT calls without pairs grow by a few
MB the same way); a leaked offscreen or picture buffer would add 5 MB or more a
call. Many draws inside one main-thread callback hold their Metal objects until
the event loop drains its autorelease pool, for base views too.

Sculpt looks
------------

``MATCAP`` and ``CURVATURE`` are Workbench draws for judging form, with fixed
settings like the other looks (``view_looks``), so they read the same in every
scene and never show the person's own viewport choices. ``MATCAP`` lights the
surface with Blender's clay matcap (``clay_studio.exr``) in one grey, without
shadows or cavity: the form alone, as sculptors judge it. ``CURVATURE`` is flat
light, one grey and Blender's cavity in world and screen space (ridges lighter,
valleys darker): where the surface bends. ``DEPTH`` (targets required, never a
strip) is drawn by the host like ``WEIGHT`` (``view_depth``, from the
silhouette above): only the targets, each pixel's distance from the camera as
grey, white at the nearest point and dark grey at the farthest, black where
nothing is drawn, and the record's ``depth`` states the distances white and
that grey stand for (``near_world``, ``far_world``), so a level reads as a
distance; shading flattens a nose's projection or a brow's overhang seen head
on, depth does not. ``test_blender_view_outline_shapes`` checks its nearest
distance against Blender's own ray cast. Without them, a model would see a
sculpt only in the studio-lit ``SOLID`` look or in materials; changing the person's
viewport shading to see it otherwise is an edit of their display. Both are
drawn and put back like ``SOLID`` (``view_capture.DisplayOverrides``); a
missing matcap keeps the person's light and is stated
(``studio_light_unavailable``).

Rendered looks
--------------

``RENDERED`` shading returns the scene camera rendered by the scene's own
engine: Blender's render job, the one F12 starts, at the view's resolution and
at most 32 samples (``view_spec.RENDER_SAMPLES``; Cycles denoised). No viewport
shading shows what a render shows (render-level modifiers and visibility, true
displacement, Cycles light transport and volumes, the compositor), so without
it an agent could describe its settings but not the picture. ``views`` is
``["SCENE_CAMERA"]`` alone; the frame is the current one; the size is the
scene's own pixel ratio with ``resolution`` as its longer side.

**A job, not a draw.** ``view_render.begin`` admits the request and starts a
``LookRunner`` (a ``job_runners.RenderRunner``) through the shared
``JobManager`` under the internal kind ``job_spec.PREVIEW``, which is not a
kind of ``angelo_job_start``. It therefore follows the background-job contract
(``long_jobs.rst``): one job at a time, progress, a time budget, Cancel on its
chat card and in the status bar, and the scene guard for other tools. The
tool call stays pending in ``Controller.media_jobs``, as a Media Studio
generation does, and the UI pump answers it when the job ends, so Blender's
interface is never blocked. A look needs no approval and leaves no job record,
chat notice or jobs-block entry: its own call reports it. A render that would
run Python from add-ons or scripts needs the person's render approval, so such
a scene gets no look (see **Approval** below).

**Bounds.** Samples: the scene's effective layer counts, at most 32 per layer.
Cycles' Use, Bounded and Ignore layer-sample policies retain their meaning below
that cap; lower layer counts stay lower, including layers inheriting a lower
scene count. The result's sample count is the greatest effective count of the
rendered layers. Both scene and rendered-layer denoising are enabled for a
completed Cycles look. These temporary settings restore with the other look
settings, including recovery of a file saved during the job.
EEVEE layer counts, which replace the scene's, are capped the same way. The
scene count, the Cycles layer policy and every rendered layer's count are
written for the look even where unchanged, so animation of any of them is
muted for the look and cannot lift the cap (``job_animated_settings``,
``long_jobs.rst``, "Animated settings").
Compositor admission visits every reachable node group once, irrespective of
nesting depth, and refuses an incomplete scan (over 4,096 groups or 100,000
nodes); an unchecked group can never authorize a File Output write.
Time: 120 s per look
(``RENDER_SECONDS``) and 360 s of looks per request (``RENDER_REQUEST_SECONDS``),
for every runtime. Past its 120 s a look is stopped once it has samples to
show; Blender still writes the samples that finished, and the result says the
image is partial (``rendered.complete`` false, ``uncertainty`` code
``render_partial``, ``samples_finished``). A scene whose preparation takes most
of that budget or more would otherwise be stopped at one or two noisy samples,
so a look always gets 20 s of sampling from its first sample
(``view_render.SAMPLE_SECONDS``): it ends at 120 s or 20 s after its first
sample, whichever is later. Only the request's total ends a look without an
image, and a new look does not start with less than 15 s of it left.

.. list-table:: Outcomes
   :header-rows: 1

   * - Ending
     - Result
   * - All samples rendered
     - One image; ``rendered`` has the engine, samples, seconds, how they
       split into preparation before the first sample and sampling, and the
       view transform.
   * - Stopped at its budget with samples
     - One image, ``uncertain``, ``render_partial``; possibly not denoised.
   * - The request's total ended it before a sample
     - No image and no ``error``: ``views`` is empty and the note says what to
       tell the person.
   * - Cancel on the card or the status bar
     - No image and no ``error``; the note says the person cancelled.
   * - The turn was stopped
     - The call answers that the look was stopped; its image is discarded when
       the render ends.
   * - Blender wrote no image or one that cannot be read, or the document was
       closed or replaced
     - ``error``; nothing is kept.

An outcome without an image is a result, not an error: the model reads why,
and the turn goes on.

**A look says what it cost and why.** The result and its note give the seconds
it took and how they split (``prepare_seconds``, ``sampling_seconds``).
Preparation is the scene's own render preparation and does not depend on the
picture's size or samples, so from ten seconds of it the note says that every
look of this scene costs about as much and that LIT is the way to iterate,
with RENDERED at checkpoints; the tool's description and the view guidance say
the same in general.

**The picture is the render window's.** Blender writes the look as an 8-bit
PNG through the scene's own view transform, look, exposure and display
(``view_render_restore.picture_values``), and the result names them
(``rendered.color``), so a model that sees an over-exposed frame can tell an
exposure setting from the lighting. A scene whose output overrides the colour
management writes its files in another view than its render window shows. The
override is left on, because switching it on again would copy the scene's
settings over its own, curves included; its plain values are set to the
scene's for the job and put back. Custom curves of the scene's view cannot be
set that way, and the result then says the colours are approximate
(``render_color_approximate``).

**Transparency is shown, not dropped.** The look is written with its alpha. A
PNG handed to a model has to be opaque, since a provider flattens alpha onto a
colour of its own, and a render written without alpha has black where the film
is transparent. ``view_picture.flatten`` composites every pixel that has alpha
over a grey checkerboard, as Blender's Image Editor shows it, and the result
states the fraction of such pixels (``rendered.transparent``) and that the
checkerboard is not in the render. A scene whose own render region is on is
rendered as F12 renders it, the rest of the frame transparent, and the note
says so.

**A region at full detail.** ``region`` is ``[left, top, right, bottom]`` in
fractions of the frame from its top-left corner, each side at least 1/64 of
it. Blender's render region renders that part alone, cropped: the frame is as
large as it takes for the part's longer side to be ``resolution``, and never
larger than the scene's resolution at 100%, which is the detail of its final
render (``view_render._size``). The job is admitted and bounded for the pixels
it renders. A part smaller than 8 pixels is refused. The resolution, its
percentage and the person's own render region are restored with the other
settings. A region's picture is no camera a viewport view can reuse.

**What a look leaves as it found it.**

* *Settings.* Resolution, percentage, render region, samples, Cycles
  denoising, output path, file extension, image format and the render and
  texture caches are set on the scene for the job only.
  ``view_render_restore.LookSettings`` first records them on the scene
  (``job_settings.TAG``), together with native scalar image-format and video
  settings and the writable scalar children of the embedded display, view,
  linear-colour-space and stereo settings. These pointers cannot be replaced,
  but their children can change when the file type changes. Capture excludes
  custom RNA callbacks, data-block references, collections, arrays and curve
  mappings. If scene curves require an approximation and the output has no
  curve mapping, the display copy uses identity without allocating one;
  existing output mappings remain untouched. It writes the captured values
  back in dependency order, twice,
  and reports any value Blender would not take back (``side_effects`` code
  ``render_settings_not_restored``) instead of assuming it did. A file saved
  while a look rendered is restored when it is opened, as for any job.
* *The Render Result.* A look renders into its own slot (named "Angelo") of
  Blender's Render Result image and puts the person's slot back, so it never
  replaces a render of theirs. Python can add a render slot but not remove one:
  that slot stays in the image and is the one thing a look adds to a file. A
  Render Result image that Blender made for the look alone is removed.
* *Preferences.* The render display is off for the starting call only
  (``job_settings.DisplayNone``), so no render window opens.
* *Files.* One PNG in the conversation's private reference folder. The render
  cache and texture-cache generation are off for the job.
* *Motion blur.* Never touched: a look is the picture F12 makes, blur included.
  When the scene's engine (EEVEE or Cycles) renders it with motion blur, the
  result says so (``rendered.motion_blur``: the shutter in frames and its
  position, ``view_blur.rendered_blur``) and the note adds that anything moving
  at this frame is blurred, so a blurred car is not read as a modelling fault.
* *Lighting.* Read, never changed: ``rendered.lighting`` states what lit the
  look as it started (``world_surface.lighting``): what the World shows and its
  strength (a flat colour's value; a Sky's sun as bearing and elevation), the
  Sun lights that render with each one's angle from the sky's sun, and how many
  other lights render, by type, so a model judging a dark or blue render knows
  what lit it. No brightness reading:
  the picture is the evidence.

**Refused before anything starts** (an ``error`` naming the reason): a scene
without a camera, stereoscopy, an unmuted compositor File Output node, a
Sequencer that renders other scenes' strips (with their own samples and
outputs, which a look cannot bound; ``render_reach``), add-on or script Python
the render would run (below), a job
that is running, a render, bake or export the person started themselves
(starting a render stops Blender's other jobs), a staged change in progress, a
modal interaction, a request whose total is used, a region of a few pixels,
and a build without the native job module.

**Approval.** None for an ordinary look, in every approval mode including
Read-only: a look changes no scene data and restores what it sets, and its cost
is bounded by the samples and the two budgets. A full-size render stays
``angelo_job_start``, which asks. A render that would execute Python that does
not ship with Angelo (an add-on's render engine, render, compositor or
frame-change handlers, or a scripted driver of something the scene reaches
whose own Python would run; ``render_extensions``, ``render_drivers``)
requires the person's existing render approval: the
look is refused at admission and again at its start, its error names that
code, and points at ``angelo_job_start RENDER_STILL`` (small resolution and
samples) followed by ``angelo_view`` with that job's ID. That job's approval
lists the code and is bound to it (``long_jobs.rst``, "Sequencer scene strips
and extension code"). Nothing is disabled to make a look automatic. Angelo's
own handlers and a fixture's measurement marked ``host_handler`` are not
extension code. Depsgraph-update handlers are mode-aware, because a look's own
setting writes run them: a
``depsgraph_update_pre``/``post`` handler that does not ship refuses the view in
the Read-only and Ask approval modes (a Read-only refusal adds that the job
route is refused too), while the Auto modes, whose typed edits already run
those handlers, let the view run and name them (``side_effects``:
``extension_handlers_ran``). ``bpy.msgbus`` subscribers that do not ship, to
the settings the look sets and puts back, count the same way; they run after
each write, also after the restore, so the result is then never "unchanged"
(``view_sources.rst``, "Motion strips", has the scope, including timers). A
stopped turn's answer then does not claim the scene untouched either. The same
rule covers every other view that sets settings for a moment: viewport
captures (``view_policy``; ``view_sources.rst``, "Module ownership"); the Render
Result, pass and file pictures write nothing at all (``view_save``), so they
are automatic in every mode.

**Checked after every view.** After every look and strip ``view_scene_check``
compares the document with its state before, leaving out the settings the view
itself set and put back. Without add-on Python, the usual case, only Blender's
and Angelo's code runs, the render and frame changes evaluate copies, and the
view's own writes are put back by construction, so a cheap reading confirms it:
data-blocks added or removed, the bound scene's own native settings and custom
properties, and every object's transform and render visibility, read natively a
collection at a time (about 0.8 ms of CPU per look on the factory scene and
7 ms with 2,000 objects). When add-on Python ran, it also compares every scene
object's data, parent, materials, modifiers and mesh positions and the native
settings, data-block pointers and custom properties of every data-block
(``doc/angelo/performance.rst``, "Scene check after views"), and first lets the
depsgraph evaluate what the restore tagged, so handlers that update runs act
inside the comparison (Blender runs handlers only in the first pass of an
update, so their own edits never appear as depsgraph updates: only reading the
data finds them). A difference sets ``scene_changed`` true and lists it
(``changed_during_view``, uncertainty ``scene_changed_during_view``); with
add-on Python and no difference found, ``scene_changed`` is null with
``scene_change_unverified``, never false, because animation, node links,
pixels and add-on settings are not compared; a check past its bounds says
``scene_check_partial``.

**Delivery.** The image takes the path of every view: a Codex call's result
carries it as an ``inputImage``, a Claude plan's next round as an ``image``
block. In a Claude plan a look is delivered once
(``claude_evidence.one_shot``): it is not one of the retained views and is
never captured again after an edit, since each costs a render. A look taken
before a later edit of the same plan is kept and labelled as taken before it.
When a look's reference camera is an ordinary camera with square pixels, its
``view_id`` can be reused by a viewport view for the same framing.

**Finished renders** (``view_finished``). With ``image``, RENDERED shows an
existing picture without rendering, scaled down and never enlarged, or its
``region`` at up to the source's own pixels:

* a job ID: what a render job of this conversation published. A
  ``RENDER_STILL`` has one file. A ``RENDER_ANIMATION`` has one file per
  frame, chosen with ``frame`` (its first frame otherwise), also from a job
  that kept only the frames that finished; a frame it did not write is
  answered with the range it did;
* a job ID with ``file``: one output of a job that published several (`One
  output of a job`_);
* ``LAST_JOB``: the render this conversation started most recently, including
  ``file`` and ``frame`` selection. The alias resolves once when the call
  starts; a newer job cannot redirect a waiting call;
* ``RENDER_RESULT``: what Blender's Render Result holds in the person's slot,
  such as their last render or the last job's frame.

A view of a queued or running render stays pending in the existing controller
(``view_wait``), then uses the same finished-picture reader. All waits in one
request share 600 seconds: overlapping intervals count once, idle gaps cost
nothing. A measured sampling pace well beyond the allowance returns progress
early; otherwise the allowance ends with progress and no picture. Both answers
tell the model to end its turn rather than poll. Stop ends the wait, while
Cancel on the job card ends the production job. Failed or cancelled jobs
retain their bounded public reason; a retained animation frame may be complete
while ``rendered.job_complete`` is false. No new render or approval is created.

The wait owns the exact manager and record, within its original conversation.
Deleted or replaced ownership returns no replacement-job facts. Output names
are checked against that job's terminal receipt, and decoding retains the
descriptor-held private-copy boundary below. A second interface-lock check
runs immediately before reading the picture. The job remains independent of
the wait and can finish after the request ends.

``job_host`` reports only a job's path; the view delivers its picture through
the existing provider image path. These are the picture conversions:

.. list-table:: Sources
   :header-rows: 1

   * - Source
     - What is delivered
   * - 8-bit PNG or JPEG
     - Its pixels as they are (they are display values already).
   * - 16-bit PNG
     - Its display values at 8 bits; ``rendered.source_depth`` is 16.
   * - OpenEXR (scene-linear)
     - An 8-bit preview: Blender itself saves the file once through the scene's
       current view transform, look and exposure (``Image.save_render``, how
       the render window shows it). ``rendered.preview`` is
       ``DISPLAY_REFERRED``, and the note says the picture is a preview and
       not the file's values.
   * - The Render Result
     - The same save of the person's slot; ``rendered.color`` names the view.

Without ``file`` only the combined image is shown. Separate passes, other
view layers and stereo views of the Render Result or of a one-file render are
not, and the result says when the render has some (``other_passes``,
``view_layers``, ``stereo_views``, ``multilayer`` for a multilayer OpenEXR
file, and a sentence in the note). Video files are not read. Transparent
pixels go over the checkerboard, as for a look.

Every such picture is bounded before it is decoded: ``view_picture.header``
reads its size and depth from the file's first bytes, and ``admit`` refuses
more than 256 MB, 68 million pixels at 8 bits or 36 million at 16 bits or
float (an 8K frame is 33 million). ``imbuf`` decodes, crops and box-filters it
outside Blender's Main, so no data-block is created; only an OpenEXR file is
loaded as a temporary Image for Blender's save, and removed before the call
returns. That save sets the scene's image format for one call and puts it
back exactly, so it is refused while a job has its own settings applied.

.. _One output of a job:

**One output of a job** (``view_job_output``). A production job publishes
several pictures and reports each as an entry of ``outputs`` with its ``file``
(``long_jobs.rst``, "Output identity"): a stereo render's views, a compositor
File Output node's items, a baked image's maps, a UDIM image's tiles. ``file``
is that name exactly as the job reported it, with ``frame`` for an animation's
``####`` pattern (its first kept frame otherwise). The picture is reached only
through ``job_ui.output_file``, which accepts a name the job reported and
nothing else, so a model never names a path; a job that published one file is
shown without a name. The result's ``rendered`` carries ``job``, ``output``,
``file`` and what the output is (``view``, ``node`` and ``slot``, ``tile``,
``map``, ``name``), and its note begins with it ("View right from job j3
(renders/shot/frame_0001_R.png)").

.. list-table:: What is delivered of one output
   :header-rows: 1

   * - Output
     - What is delivered
   * - A render's view or File Output item
     - As any finished picture (the table above).
   * - A baked map of data (``job_spec.DATA_MAPS``: normal, position, UV,
       roughness, ambient occlusion, shadow), any tile of its image
     - Its stored values, with no view transform: an 8- or 16-bit file as it
       is, an OpenEXR file's values from 0 to 1 as they are
       (``view_picture.scaled(stored=True)``). ``rendered.values`` is
       ``STORED``, ``rendered.clipped`` the fraction of pixels outside 0 to 1,
       and the note says both. A view transform would bend the numbers the
       picture is looked at for.
   * - A baked colour map
     - An 8- or 16-bit file as it is; an OpenEXR file as the preview above.

Refused, each with what works: a job with several pictures and no ``file``
(the refusal names its outputs with what each is, eight at most); a name the
job did not report; a file that holds several layers or views in one OpenEXR
(a view shows one picture: write a File Output item each, or the views in
individual files); a bake without an ``output`` (it published no file); a job
that has not ended or is another conversation's. A picture delivered this way
counts as ``job:<id>`` in the receipt and marks the job as shown with the
output (`How a request got to its result`_).

**A finished render operator.** Blender's render operator stays on the
window's modal stack until the first event after its job ended, and an
unattended window gets none. ``input_policy.active_interactions`` therefore
does not count it once no render job runs; otherwise every later tool that
checks for an interaction would wait for one that is over.

**Limits.** One render at a time. RENDERED renders the scene camera at the
current frame only. A staged preview is not rendered: a look is refused while
a staged change is in progress, and a staged scene is still drawn as solid
geometry. LIT draws on the main thread, and EEVEE volumes can take seconds
(``performance.rst``, "Rendered looks"). The first GPU render after an install
compiles its kernels, which can outlast a look. A scene with Lock Interface
locks Blender while a look renders, as for any render. An engine without a
sample setting renders with its own settings, bounded by time only. A picture
is one image: the combined image, or one output a job published as its own
file. No pass, layer or channel inside a file is chosen, no strip of frames
is made and no video is read. Windows and Linux were not measured.

Measurement and recovery
========================

Performance receipts contain only bounded numeric timings/counts, fixed enums
and a local session UUID. A failed tool result adds one count of a closed,
content-free class (``failure_codes.py``), never its error text. No transcript, tool arguments, code or credentials are
saved; there is no upload. A coalescing background writer retains at most 256
receipts in a size-bounded private file (512 KiB). Receipts are not all one
size, so when 256 of them would not fit, the file keeps the newest that do; the
running application still holds all 256. Recorded receipts of long sessions are
1.0 to 1.5 KB, and one with every section filled is about 2.0 KB, the average
the budget allows. The metrics tool reads only its chat; the human Performance
Summary can inspect all local chats.

Snapshot capture begins after timing starts. Receipts separate human approval
wait from machine work and distinguish actual measured staging, execution,
commit and verification. Verification is the staged job's own check,
stage-validation and read-back time, so a fused poll still reports it; the
poll's hydration and serialization count as staging only. Missing phases are
unmeasured, not zero. Receipts
also include first observed scene-change and first verified-result timestamps
when a fresh native scene-mutation result actually supplies that evidence.
These measure host receipt, not an inferred instant inside a kernel or artistic
goal completion. Preview, read-only, file-only, uncertain and replayed results
cannot fabricate a scene-change timing. Startup records cold/warm status, and
the report splits total latency by it.
Provider turn submissions are counted for both runtimes;
Claude's explicit typed-plan rounds are additionally counted. Internal Codex
model-round counts are unavailable and are not guessed from tool calls.

Provider usage (``provider_usage.py``) comes only from each runtime's own running
totals: the Codex app-server's ``thread/tokenUsage/updated`` thread total and
Claude Code's result ``modelUsage``, ``duration_api_ms`` and ``total_cost_usd``,
which accumulate per process. A turn's usage is the difference between the totals
around it. A turn whose baseline is unknown, that received no total, or whose
total decreased is unmeasured, never zero, and a receipt keeps a usage field
only when every provider turn of the request reported it. Baselines are unknown
for the first turn after a Codex thread resume and for the first turn of a
resumed Claude process, because Claude Code restores the session's saved totals
from its transcript on ``--resume``; a fresh thread or process starts at zero.
For a Claude round without a baseline the host uses the token counts the API
itself streamed for that round's messages (``claude_stream.RoundStream``, from
``message_start`` and ``message_delta``): tokens and the answering model, never
a cost or API time, and only when every message of the round finished. Every
Stop and every timeout closes the Claude process, so the next request always
starts with such a round. A Codex thread has no such counts: its first turn
after a resume stays unmeasured unless Codex reported the thread's total before
the turn began.

A turn the host cut short (Stop, a timeout) reports what is known and is counted
in the receipt's ``interrupted_turns``. Claude: the input-side counts of the
messages that had started; an interrupted message's output count is unknown,
because the API reports it at the message's end, and is never estimated. Codex:
the difference of its thread totals so far. With ``interrupted_turns`` present,
every usage field is a lower bound: the fields the request's other turns
reported are kept instead of being withheld, and a cut turn with nothing
measured still leaves ``turns`` and ``interrupted_turns``. A completed turn that
reported nothing still withholds its fields.
``test_native_turn_activity.py`` (Claude) and
``test_native_turn_activity_codex.py`` (Codex) cover Stop, a timeout and the
first turn after a resume. Input tokens include cache reads and writes for both
runtimes; cached and cache-write tokens are subsets of them. Third-party models
routed through Codex may report zero cached or reasoning tokens when they do not
know them, so a zero there is not proof of a cache miss. Codex turn time
includes host tool calls and approvals; Claude turn time excludes host plan
execution. Claude API time includes network, queueing and retries rather than
pure inference, and its cost is Claude Code's estimate, not a bill. Codex
reports neither. Codex usage
updates are handled before the startup queue so they cannot displace early tool
calls or a turn's completion.

Each receipt also records its route (``telemetry.clean_route``): the connection
mode, the requested model (empty for the runtime's default), the effort actually
sent (Claude's ``--effort`` mapping or Codex's catalog fallback), Fast, the
native session's prompt-cache TTL, a cache-gap bucket (time since the same
native session's previous model request ended: ``new_session``, ``unknown``
after a restart, ``under_5m``, ``5m_to_1h`` or ``over_1h``) and the models the
runtime reported serving its turns (Claude Code per model, which also shows an
automatic fallback; Codex the turn's model). Read-only consultations
(``angelo_delegate``) are recorded under ``consultations``, attributed to the
request that asked for them, with their own route and usage under the same
completeness rule; they are never added to the lead's usage. Only allowlisted
identifiers and numbers are kept, never prompts, replies or task text. See
``model_routing.rst`` for what these fields are for.

How a request got to its result
-------------------------------

A receipt's ``path`` section (``request_path.py``) says how the agent worked,
from what the host sees for free: closed names and numbers only, recorded by
the request context at the one place every call is dispatched and every
provider turn is counted (``routing.RuntimeContext``), for both runtimes.
Nothing in it is asked of the model, and no tool result, schema or instruction
changes.

* ``edits``: completed calls that changed, or may have changed, the scene.
* ``images_returned`` counts picture records returned by host tools, separately
  from delivery. ``image_delivery`` is ``confirmed`` when at least one picture
  has proven delivery, ``unknown`` when writes remain unconfirmed and none is
  proven, or ``none`` when neither kind of delivery was recorded. Positive
  ``images_unconfirmed`` with zero proven pictures always means UNKNOWN,
  never a definitive negative. Counts alone do not prove a final-result look.
* ``images_delivered`` and ``images``: the pictures with proven model delivery.
  Each entry is ``[turn, edits, {kind: count}]``: the provider turn (counted as
  ``provider_turn_requests`` is), the edits completed when these pictures were
  captured, and the pictures by kind. Pictures of different ages in one turn
  occupy separate entries. Attachments and existing job/file/Render Result
  pictures have unknown original capture age (``null``):
  ``view:<SHADING>:<FRAMING>`` for a viewport view, ``job:<id>`` for a job's
  picture, ``sculpt``, ``preview`` (a staged program's), ``media`` and
  ``attachment`` (the person's own). A picture counts once the provider
  acknowledged the input that carries it. Claude's first API message start,
  model response or successful result acknowledges that round. Codex's matching
  model output or tool request acknowledges the turn's initial input. Missing
  acknowledgement leaves delivery unproven; a receipt does not prove
  comprehension. The latest 12 turn/capture groups with pictures are kept.
  An entry whose ``edits`` equals the section's ``edits`` was captured after the
  last edit counted by this request. Delivery cannot make an older capture
  current. Attachments and finished job pictures do not prove that the current
  live scene was rendered: they show their source image, whose earlier history
  this request does not establish.
  The ``image_delivery`` marker also identifies these capture ages. Legacy delivery
  rows without the ``image_delivery`` marker used weaker evidence: their counts
  and closed metadata reload as unconfirmed, with unknown capture ages, never
  as proven delivery or a current result.
* ``images_unconfirmed`` and ``unconfirmed_images`` count successfully written
  Codex tool pictures and retain their capture groups in the same shape. Its
  code-mode result carries image URLs, but the model's script must forward them
  with ``image()``. The app-server emits identical public events when the script
  forwards or omits pixels, including the tool completion's ``inputImage``
  items. A fresh assistant item can also belong to the same response that
  requested the tools. Neither these events, token usage, web-search activity,
  nor turn completion proves pixel forwarding. Failed writes count neither
  confirmed nor unconfirmed delivery. Request/token usage counts are unchanged.
* ``steps``: in order, at most 24: ``[kind, outcome, name, [names], repeats]``.
  ``lookup`` names a capability family and the kinds asked for, ``api`` a
  Blender API documentation domain (never the looked-up name), ``typed`` a typed
  tool and its action kinds, ``script`` a reviewed script. ``outcome`` is
  ``ok``, ``invalid`` (the host's validator returned it before anything ran),
  ``declined``, ``raised`` or a ``failure_codes`` class. A script after a
  ``typed`` step that was not ``ok`` was a fallback after a refusal; a script
  with nothing typed before it went straight to a script. No reason is asked of
  the model: that would cost output tokens on every script. Names are admitted
  only from the owning action, tool, check, recipe and enum registries, both
  when recorded and when reloaded. Identifier spelling alone grants no admission.

``usage.requests`` is the number of model requests the request's provider turns
reported, under the same completeness rule as the other usage fields. A Claude
round counts the API messages its stream started. Codex reports neither model
requests nor rounds: it sends one ``thread/tokenUsage/updated`` after each
model response, and a turn's ``requests`` is the number of those updates whose
thread total grew. ``counts.failure_script`` counts scripts that raised.
Neither is part of the report the metrics tool returns.

``TelemetryStore.running_usage(session_id)`` (``controller.telemetry``) is the
read-only usage so far of that chat's request in progress, or None: the fields
its finished provider turns reported plus what the turn in progress has
reported so far, with ``turns`` and ``running``. A Claude round reports its
input counts when each message starts and its output count when the message
ends; a Codex turn reports the growth of its thread total after each model
response. While ``running`` is true every number is a lower bound.

A background job's record says whether it reached the model
(``job_store.mark_shown``): ``shown.result_at`` is when the finished job's
entry of a jobs block was acknowledged (set when the request ends without
Stop, as its accepted ledger is committed, even if a later round fails).
Pending context updates from an unacknowledged round never replace that ledger.
``shown.image_at`` with ``shown.images`` records acknowledged delivery of a
view showing the job's picture, which a view result
states by naming the job in ``rendered.job`` or in the view record's ``job``.
For a job that published several pictures ``shown.files`` lists each output a
view showed (``rendered.file``), once, as the job reported it; the receipt
itself counts ``job:<id>`` and names no file. An empty ``shown`` means delivery
was not established. ``test_native_request_path.py`` and
``test_native_job_shown.py`` cover the section, the running read and the job
marks on both runtimes; ``test_native_view_job_output.py`` the outputs.
Job records mark this proof contract with ``shown_version``. Earlier unversioned
``shown`` claims reload under ``shown_unconfirmed`` and leave current ``shown``
empty; historical timestamps and counts stay inspectable without being treated
as proof. These bookkeeping fields never enter the model's jobs block.

``main_thread_block_ms`` is a peak, not a sum: the longest single section the
chat spent in its event pump, tool pump, approval execution or commit while the
request was active. The event-pump section that ends the request counts whole,
with the synchronous persistence it does (the runtime entry, the context
ledger and its jobs, the session save): the receipt is finalized after that
work, while ``total_ms`` still ends where the turn ended, before it. Drawing is
excluded. Admitted reviewed-Python invocations are
counted per request with fixed, host-derived categories
(``fallback_categories.py``) from the Blender API names the script references.
They describe the domain, not the model's intent; only the label is kept, and a
script counts once in each category it touches. These signals select which
bounded native tools to add next. ``test_native_provider_usage.py``,
``test_native_agent_telemetry.py`` and ``test_native_fallback_categories.py``
cover these contracts offline against the installed runtimes' documented
shapes; they do not verify what a live provider reports.

Every Request remains the checkpoint default. Before First Approved Edit is an
explicit performance/restore-boundary tradeoff. Native staging captures its
recovery point before staging, since saving a live temporary stage is not a
valid request baseline. Snapshots are stored deduplicated at the block level
by a background worker that never touches ``bpy``; restore rebuilds and verifies
the exact captured file. The human-set retention limits (2 GiB by default) are
enforced automatically, oldest first; protected recovery points, every chat's
active request and file-journal dependencies survive. No semantic geometry-delta
storage or background ``bpy`` snapshotting is used.

Unrestricted reviewed Python is privileged and can have external effects.
Neither Stop, Blender Undo, nor a document snapshot makes it transactional.
Full exact sculpt fingerprints remain required because native dirty flags do
not cover every live sculpt mutation. See ``agent_execution_feedback.rst`` for
the design constraints and their safety rationale.

Reproducible validation
=======================

Host tests run without provider access::

  <bundled-python> -m unittest discover -s tests/python/angelo_agent -p 'test_native_*.py'

``tests/python/angelo_agent/run_host_tests.py`` runs the same suite and lists
every skip. The ``Host tests`` GitHub workflow runs it on each pull request and
push to ``main`` with NumPy and jsonschema installed and ``--max-skips 0``, so a
new skip fails the build. Host CI does not replace the native or GUI suites.

Native fixtures use the Angelo binary (not an older Blender.app in the build
directory), factory startup, isolated config/state/accounts/recovery directories
and ``--disable-autoexec``. The query, API, action, verification and task-matrix
fixtures support background mode. Program and workspace UI fixtures additionally
prove save/Undo/Redo, real preview pixels and controls in the GUI; the workspace
fixture needs ``--enable-event-simulate``. No paid requests or user projects are
required. Inspect both JSON results and logs for native assertions; the runner
below does both.

``tests/python/angelo_agent/run_native_suites.py`` (host Python 3.9 or newer,
standard library only) runs the fixtures against a compiled binary, given as an
absolute path with ``--binary`` or ``ANGELO_BINARY`` (there is no default
location; for example, the macOS development build)::

  python3 tests/python/angelo_agent/run_native_suites.py \
    --binary "$PWD/../build-angelo-native/bin/Angelo.app/Contents/MacOS/Angelo" --jobs 2

A suite runs inside the app, which loads the add-on and startup modules bundled
in it, never the checkout's. So before anything runs the runner compares them
with this checkout's (``native_addon_guard``: every file of
``scripts/addons_core/angelo_agent`` both ways, so a module the checkout deleted
that an install left in the app counts, and every Python module of
``scripts/startup`` one way) and refuses a mismatch, naming both folders, their
digests and the differing files: install the checkout into the app first.
``--allow-addon-mismatch`` runs anyway, deliberately, and its first output line
is a warning that the results are the app's; the summary records the
comparison (``bundled_addon``). Without the guard a suite would test whatever
an earlier install left in the app, not the change under test.

``native_suite_manifest.py`` classifies every ``test_blender_*.py`` file, from its
own guards and receipt code, as BACKGROUND (``--background``), GUI (needs a
window; some also call ``Window.event_simulate`` or need Angelo's default chat
startup) or LIVE (real providers or provider endpoints). Benchmarks, the
multires stress runner, the live-agent latency harness (paid provider
requests) and legacy live
scripts are excluded; ``--list`` prints the classification. BACKGROUND suites
run by default and ``--gui`` adds GUI suites. A LIVE suite runs only with
``--allow-live`` plus an exact ``--include`` of its name, and still requires its
own opt-in variables from the caller.
``--include``/``--exclude`` take name globs; documented serial suites run alone.
A suite whose code depends on particular platforms names them in the
manifest's ``platforms``, and the runner skips it elsewhere with that reason.
Windows skips the macOS-only suites (app bundle identity, login Keychain,
instruction-count ratchet, and the external CLI, whose Unix-socket transport
Windows does not open) and the three suites that put POSIX shell-wrapper
stand-ins for ``claude``/``codex`` on PATH (chat cards, Director navigation,
first run).

A suite may declare variants (``native_suite_manifest.Variant``): each is a run
of its own, named ``<suite>@<variant>``, with the variant's environment set
over the caller's, and a suite's own name selects all of its variants. Every
suite that drives worker chats declares ``WORKER_CHAT_HOST``: ``hosted``
(``ANGELO_WORKER_CHAT_HOST=1``, design A′, the default) and ``lanes`` (``0``,
the rollout fallback), so both paths keep their coverage while the switch
exists; a suite without variants takes the caller's switch through. Under
``hosted`` a worker chat is driven as the lead makes and runs it
(``hosted_worker_chat.HostedWorkerChat``): a mirror opened in a real worker
Blender, whose model is a stand-in ``codex`` app-server
(``hosted_codex_stand_in``) on the lead's PATH, launched by the lead's
``RuntimeService`` and relayed to the chat's provider in the worker. It is the
kind of ``codex`` the lead's own launch starts on each platform: a shell wrapper
on macOS and Linux, and on Windows a ``codex.exe`` built from pip's console
launcher, because Angelo starts a plain batch file only when every argument is
a plain token, which the app-server's quoted overrides are not; so the hosted
suites run on Windows too, through its pipes, delete-sharing reads and Win32
parent checks. The suite
queues the model's scripted tool calls for a turn and sends the chat a message,
and cards are drawn, then approved with Angelo's own operator. Checks that exist
on one path only (a lane's round trips, two chats on one worker) say why where
they branch. ``test_blender_worker_chat_hosted`` covers what only the hosted
path has: python consent through the mirror, the merge hold and part lease, a
switch that reopens a chat with ``resume``, pool reuse, a worker crash
mid-call, Stop on a busy worker and a consent revocation. Those suites record
the card's drawing by hand before they approve; ``test_blender_worker_approval_lead``
(GUI, Director Mode) draws nothing itself: a worker's script card in Auto shows
on its rail tile while the lead's chat stays shown, is approved from the lead
as the benchmark's tester approves it and, next, by a person's clicks on the
tile and the card, and each script runs in the worker within seconds.

Each suite gets fresh HOME (and Windows' USERPROFILE, APPDATA and
LOCALAPPDATA), TMPDIR/TMP/TEMP (Blender recovery and crash files), Blender
user resources, Angelo state/accounts/library and QA directories (state,
accounts and user resources nest inside QA for a suite that requires it), and
inherits only a small environment allowlist (on Windows also the system,
program-folder and processor variables), so neither real profiles nor
provider keys reach it. HOME and TMP sit in the suite's profile folder,
``<run>/p/<8 hex of its name>``, beside its own folder, so their depth never
grows with its name: Angelo.exe is not long-path aware, and a parallel worker's
files sit up to 131 characters below ``%LOCALAPPDATA%``
(``native_suite_paths.ANGELO_DATA_DEPTH``, from ``worker_paths``); a profile
nested in a long suite name once passed Windows' 259-character paths. On Windows
a suite whose profile would still pass that limit is refused before it starts,
with both lengths, as the product refuses such a worker
(``WRK-PATH-TOO-LONG``). The runner
reads logs and crash files from both folders. A run directory is created under
``--output``, by
default ``native_suite_paths.short_temp_root()``: ``/private/tmp`` on macOS,
whose 104-byte Unix-socket paths the external CLI fixture needs (the per-user
``$TMPDIR`` is already about half of that), and the temp folder elsewhere. The
two host drivers that refuse other fixture folders (external CLI and
saved-project continuity) require a run directory under that root; outside
macOS they also accept the QA folder the runner made for them. Suites run
directly default to a folder under ``tempfile.gettempdir()`` when
``ANGELO_QA_DIR`` is unset. A timed-out suite's whole process tree is killed:
its process group on macOS and Linux, ``taskkill /T /F`` on Windows, where an
unhandled exception's NTSTATUS exit code counts as a crash.
``--timeout-scale`` multiplies every suite timeout and reaches the suite as
``ANGELO_QA_TIMEOUT_SCALE`` (never from the caller's environment). A fixture's
own bounded waits (``qa_waits.IdleWait``, such as the Sculpt checkpoint
fixtures' 10 s wait for idle native jobs) multiply their base budget by it, so
a loaded machine gets more time without any change to a fixture. The release
runs set it per platform: 1.5 on the macOS release Mac (two suites at once), 2 in
the Windows release stage (``windows_release_validate.ps1``), whose build VMs run
the same work about three to four times slower than the reference Mac, stock
Blender included (measured 2026-10-19 on an n1-standard-16 + T4).

Absolute timing limits (a suite asserting that an operation takes under N
seconds) are calibrated on the reference Mac, an Apple M-series machine. Such a
check compares against ``qa_waits.machine_limit(N)``: N times the machine
factor, which ``run_native_suites.py`` measures once per run before the suites
start (``--machine-factor auto``, the default) and passes to each suite as
``ANGELO_QA_MACHINE_FACTOR``. The factor is ``qa_reference_workload.py``'s
median time in the app under test divided by its time on the reference Mac
(``qa_machine.REFERENCE_SECONDS``: 0.1806 s, measured 2026-10-20 in Angelo
1.0.0 on the release Mac). The workload is pure computation (a
mathutils BVH and 200,000 ray casts, a numpy pass, a Python loop) and creates no
data-block, so an add-on's work on data changes never enters the factor: it
measures the machine, never the product. The factor is the measured ratio,
never padded and never below 1, and a run that cannot measure it keeps the
calibrated limits (factor 1). ``--machine-factor off`` or a number overrides it;
``summary.json`` records the factor and how it was obtained. Suites still
holding a bare absolute limit convert to ``machine_limit`` as they are touched
(tracked after 1.0.1). Window suites, and
the host drivers that launch their
own windows, pass ``--no-window-focus`` from ``gui_launch.window_flags``, the
one source of window flags for every automated launch (suites, live evals,
benchmarks, stress scripts, smokes); ``test_native_gui_launch_focus.py`` fails
a launcher that builds a window command without it, unless
``gui_launch.FOCUSED_LAUNCHES`` names it with a reason. On macOS the app then
runs as an accessory application whose windows open behind other
applications' windows and are never ordered above them
(``GHOST_WindowCocoa::orderFrontAndMakeKey``, pinned by
``test_native_window_order.py``). macOS still activates an application the
front-most one started when it shows its first window; the app hands that
activation straight back (23 to 32 ms measured, longer while it is busy) and
keeps an activation the user makes by clicking one of its windows. A fully
covered window gets a free drawable
from macOS only about once a second, so without focus its frames are drawn
(window captures read them) but not presented while covered, and it is redrawn
when it shows again; App Nap does not throttle it. With focus, the default,
none of this applies (measurements: ``performance.rst``, "Window state of GUI
runs"). They also pass
``--enable-event-simulate`` (input isolation), so Blender ignores the machine's
real keyboard and mouse events and keeps its own clipboard: a key meant for
another application cannot start playback or change the frame mid-suite. The
main loop then does not idle-sleep, one busy core per
window. A suite that needs real input, the system clipboard or the idle sleep
sets ``real_input`` in the manifest and runs without it; none does. A suite passes only when it exits cleanly within its
timeout, writes a fresh JSON receipt whose success field, checks and
paid-request counts pass (or prints its completion marker when it has no
receipt), and none of its logs contains a ``BLI_assert``, unreachable-code or C
``assert`` failure, a guarded allocator leak or corruption report, a crash log,
or a line-initial Python traceback. The development preset builds with
``WITH_ASSERT_RELEASE`` but without ``WITH_ASSERT_ABORT``, and GUI fixtures quit
normally after a failure, so exit status alone proves nothing. Results go to
``summary.json`` and ``summary.txt`` in a new run directory, and the exit status
is nonzero when any selected suite fails. Durations with ``--jobs`` above 1 are
measured under concurrent load. ``test_native_suite_runner.py`` keeps the
manifest complete and consistent with each suite's guards.

Only host tests run in GitHub CI. Native suites run locally with this runner
before merging, against the compiled development app; there is no hosted native
CI because it would have to fetch the pinned libraries and build the app on
every run. The instruction-count ratchet
is one of the native suites (``test_blender_perf_ratchet``, macOS arm64 only);
the host suite fails in CI while ``perf_ceilings.json`` records no passing
ratchet run on the current add-on and benchmark sources (see "Instruction
ratchet as a merge gate" in ``performance.rst``).

``test_native_view_observation.py`` and ``test_native_view_capture.py`` cover
the view contract, framing, PNG encoding, cache keys, coalescing, invalidation,
display restoration and provider delivery on the host.
``test_blender_view_observation.py`` needs a GUI (not ``--background``): it
checks actual pixels for every shading, a fresh material drawn on its first
MATERIAL capture, unchanged artist shading/camera/selection/mode/frame, no new
Main IDs, cache hits across idle UI ticks, misses after an edit, identical REUSE
cameras and expiry after an epoch change, and records 512-pixel timings.

The rendered pictures are covered on the host with a stand-in Blender whose
image format changes its dependent values as Blender's does
(``rendered_view_fakes.py``) and a stand-in ``imbuf`` (``picture_fakes.py``),
so the pixel code that runs is the real one. ``test_native_view_render.py``:
the contract and its stated costs, the job and its settings, an exact restore
from an OpenEXR and from a video output format, the render slot and each
refusal. ``test_native_view_render_bounds.py``: every ending of the outcomes
table and both budgets. ``test_native_view_render_region.py``: a region's
frame, render region and restore, transparency and the scene's colours under
an overriding output. ``test_native_view_picture.py``: headers and bounds, the
crop's corners, a planted small defect lost in the scaled frame and present in
its region, and the checkerboard under planted transparent pixels.
``test_native_view_finished.py``: each source of a finished picture and each
refusal. ``test_native_view_job_output.py``: one output of a job by the name
it reported (a view, a File Output item, a frame of a pattern, a baked map, a
UDIM tile), stored values without a transform, every refusal, and the shown
mark. ``test_native_view_budget.py``: the view budget, its message and a
corrected plan completing its turn; the refresh's time bound on a light and a
heavy scene, its order, a stale view asked for again, and a failed capture
that goes to the model. ``test_native_view_render_delivery.py``:
the Claude and Codex hand-off with the stand-in providers, a view that cannot
be captured as an ordinary result on both, a real ``Controller`` holding the
call pending (also in Read-only mode, and as a call that rests the turn's
clocks) and stopping it with the turn, and both runtimes' payloads decoded
again to the planted pixels. ``test_native_read_look_edit.py``: a read, a
look, an edit and a look in one plan or turn with the live read tools. ``test_blender_rendered_view.py`` (GUI)
renders three tiny scenes (a red world, a blue sun, a magenta volume) and
shows that SOLID, WIREFRAME and MATERIAL show none of them while LIT and
RENDERED show all, with EEVEE and Cycles; it also checks both hand-offs, a
job's image and the Render Result, and records seconds per look.
``test_blender_rendered_view_pixels.py`` (GUI) plants a wrong light, a missing
sky, a transparent film, an over-exposed frame and a two-pixel defect, and
reads each from the PNG inside both runtimes' payloads; it compares a look and
the shown Render Result with Blender's own "as render" save under three view
transforms and an overriding output, shows a quarter-opaque PNG, an OpenEXR
above 1, a 16-bit PNG and one frame of an animation job, and edits after a
look.
``test_blender_job_output_views.py`` (GUI) runs real jobs and reads one output
of each from both runtimes' payloads: a pyramid baked into a normal map, equal
to the published file byte for byte under an AgX view with two stops of
exposure; a position map in OpenEXR as its stored values; a red patch only the
right eye sees, in the right view, in its File Output item and in the second
frame of an animation, and absent from the left view and the mask; the tile
of a UDIM bake whose face emits another colour; and each refusal.
``test_blender_rendered_view_safety.py`` (GUI) compares everything below the
scene, every data-block, the selection, mode and frame, the viewport, the
preferences, the saved file, the workspace and the person's own render before
and after a look, from an OpenEXR, a JPEG and a video output; it stops looks at
their budget, from the card and with the turn, and opens a file saved while a
look rendered.

Run ``benchmark_blender_agent_execution.py`` alone after builds/tests stop. It
reports one warmup plus five samples, snapshot storage, stage serialization,
maximum local poll time and payload sizes for the default 100-object fixture.
It then repeats target revision, search, rich object query and one staged
program in three scale cohorts: 2,000 objects sharing one node material, about
one million dense-mesh vertices, and 2,000 objects in a 511-collection tree.
Costs that grow with scene size stay hidden at 100 objects, so compare cohorts
before and after query or staging changes. It excludes provider latency, GUI
timer waits, GPU previews and Undo cost; it cannot establish an end-to-end speedup
or performance against MCP. Cross-platform and paid provider acceptance require
separate validation rather than extrapolating from local macOS fixtures.
Every operation also reports process CPU time and retired instructions, and
``results.json`` feeds the instruction-count ratchet; the snapshot-cost,
stage-compression and opt-in live-agent harnesses (paid provider requests) are described with
their methods in ``performance.rst``.

Windows validation
==================

The agent's Windows port (``anchored_windows.py`` and ``windows_nt.py`` for
storage, ``cli_windows.py`` and ``child_process.py`` for the CLIs) is checked on
a disposable Windows x64 machine in two parts. Neither signs in to a provider,
sends a prompt or reads a credential. Each script writes every step's output and
``agent-results.json`` (PASS or FAIL per step) to its ``-ResultsDir``.

* **Without a desktop** (``scripts/angelo/windows_agent_validate.ps1``, as the
  machine's administrator): installs CI's host Python (3.13 with NumPy 2.3.4
  and jsonschema 4.23.0, as ``.github/workflows/host-tests.yml``) and Node.js
  LTS, builds the Lite configuration with the options the macOS development
  build adds (OpenSubdiv, WebP), then runs the host suite with that Python, the
  checkpoint and adversarial storage suites, ``windows_agent_checks.py ntfs``
  with that Python and inside ``Angelo.exe`` (``checkpoint_policy.rst``,
  "Validation"), and the BACKGROUND native suites.
* **In a signed-in desktop** (``scripts/angelo/windows_agent_gui.ps1``, as that
  user): installs the official CLIs the documented ways (Claude Code by its
  native installer and by npm, Codex by npm), then runs
  ``windows_agent_checks.py cli`` inside the app started through
  ``Angelo-launcher.exe`` (``ai_providers.rst``, "Windows"), then every native
  suite with ``--gui``.

The real-file-system and CLI checks can also be run on their own
(``tests/python/angelo_agent/windows_agent_checks.py``; ``ntfs`` needs an
administrator, and ``cli`` is meant for the app started from a signed-in
desktop after the official CLIs were installed for that user)::

   python windows_agent_checks.py ntfs --report ntfs.json
   Angelo.exe --background --factory-startup --python windows_agent_checks.py -- ntfs --report ntfs-app.json
   Angelo-launcher.exe --factory-startup --no-window-focus --python windows_agent_checks.py -- cli --report cli.json --desktop

**What it proves.** The storage suites (anchoring, file journals, checkpoint
policy, recovery, ordered recovery, the snapshot store, Studio export journals,
state migration and the Windows tests) run on the real NT backend, and the
file backend is exercised against real NTFS and FAT32 by ``ntfs``, both with
CI's Python and with the app's own. ``cli`` checks, inside the app as a desktop
launch starts it, CLI discovery, ``--version`` probes, Codex's app-server, shim
quoting, ``CREATE_NO_WINDOW``, the Job Object tree kill, UTF-8 pipes, Git Bash
and the default browser. The BACKGROUND and GUI native suites run against the
Windows build, except those skipped by platform.

Skipped on Windows, each with its reason: the Unix-socket CLI transport and
its private paths (Windows refuses to listen; that refusal is tested), macOS and
Linux CLI discovery and the login-shell probe (Windows has its own, tested), POSIX
mode bits, the POSIX stand-in of the NT API (the real backend runs instead),
the macOS MCP comparison bench, and the native suites whose code is macOS-only
or uses POSIX shell stand-ins for the CLIs (``native_suite_manifest.py``).

**Not covered.** Windows 10 and 11 as such (the procedure runs on Windows
Server 2022 with the UTF-8 ANSI code page, long paths off and Defender on);
ReFS, exFAT, network shares and OneDrive placeholders; a real provider sign-in
or turn on Windows; the three GUI suites that put POSIX shell stand-ins for
``claude`` and ``codex`` on PATH (chat cards, Director navigation, first run),
which need ``.exe`` or npm-shaped stand-ins; the external CLI (no Windows
transport yet); the instruction ratchet (macOS counters); and the full release
configuration (Cycles and the other release features are off in Lite).

Trusted scripts for an open chat
================================

Python uses the existing reviewed executor with full Blender privileges, never
a sandbox or automatic confinement. The Approval Policy menu offers a separate
``Trust scripts for this chat`` dialog. Its explicit confirmation allows future
Python calls in the current open chat/document to run without individual code
review. This can access files, networks, programs and application preferences.
External effects are not guaranteed undoable, arbitrary Python has no universal
scene Undo guarantee, and Stop cannot interrupt atomic Python.

Consent exists only in the Controller's memory, defaults off, and is not saved
in options, preferences, project data or transcripts. Auto bounded edits never
grants script trust. Restart, New Chat, document invalidation and Read Only
clear it; the same menu revokes it. Already queued scripts require manual review.
Managed ``AllowAutoApproval`` / organization ``allowAutoApproval`` can disable
script trust; effective policy is checked at dispatch. Script calls carry no
consent argument. Exact-code manual approval runs once through the same executor
regardless of whether the script appears to touch only the scene.

Controller dispatch checks current pending-call identity, invocation digest,
document/turn fences and consent again after precondition/checkpoint/journal
work. Results for executed scripts include ``execution_mode=trusted_unconfined``,
``confined=false``, the consent source, ``external_effects_undoable=false`` and
``stop_interruptible=false``. These fields describe execution authority, not
proof of task completion or recovery. Existing checkpoint/recovery owners remain
responsible for truthful recovery outcomes.

**Worker chats** (parallel edit, ``worker_trust``; the maintainer's decisions of
2026-10-25). A worker chat's scripts follow its own approval mode, taken from
the lead when it starts. In Ask every worker script asks, whatever is trusted,
and a worker chat in Ask cannot be trusted on its own. In Auto a worker chat
inherits the lead's trust: from its creation (``worker_sessions._new_chat``
links it) and again whenever the lead turns trust on later. The inheritance is
read live, never copied: the lead's own consent token covers the worker's
scripts while the lead's trust lasts, so turning it off, New Chat or a document
change in the lead, Read only, or ``AllowAutoApproval`` off ends it for every
worker too, and a script queued under the old token asks. Turning trust off in
a worker's own chat stops that worker following the lead until the lead turns
trust on again; a worker chat trusted on its own keeps that whatever the lead
does. Always allow scripts covers an Auto worker chat as any Auto chat, and a
script that names preferences or ``angelo_agent`` always asks. The script card
a worker's tile opens acts on that worker's chat: Approve, Review and Decline
carry the card's own chat (``session_id``), never only the chat shown now, so
approving from any surface approves the worker chat's own call. A worker
chat's results never send its model to a request snapshot, which Restore
refuses for a worker chat: a recovery naming the pre-request snapshot is
replaced by the worker's own words (``script_errors.in_worker``; a script that
raised: ``RECOVERY_IN_WORKER``). The words follow Restore's own test
(``worker_routing.worker_snapshots``), so a chat hosted in its worker gets them
too. The recovery barrier's closing reason follows the same test
(``RecoveryBarrier.restorable``, ``recovery_reads.restorable``): only a chat
Restore takes is told its pre-request checkpoint can be restored, and a chat
whose test cannot be answered is told nothing about restoring (fail closed).

**What a worker chat refuses** is one set, on both paths
(``worker_refusals``): with nothing run and ``UNSUPPORTED``, a tool outside its
worker's part reads, views, typed edits and scripts and the chat's own tools
(a job, Sculpt, Studio, flows, team tools, previews' acceptance), a PREVIEW, a
script whose code names a file load, and the file tools (a sealed workspace).
On the lane path ``RemoteExecutor`` refuses before anything is sent and
``WorkerHost`` again in the worker; a chat hosted in its worker gets the
window's own executor confined by the same set (``worker_chat_executor``,
through ``controller_external.bound_target`` as a lane chat gets its
``RemoteExecutor``), so a refusal comes before its card on every route. The
confined executor also contains a hosted script as ``worker_script.contain``
does on the lane path (a hidden load reloads the part, a lost part ends the
worker without saving, a save-as has the part saved again,
``worker_chat_part``), and runs the worker's GPU check, which initializes
Blender's GPU module, before a view draws (``WorkerHost.gpu``). That check takes
Angelo's offscreen verdict for the worker's binary from ``chat.open``
(``gpu_verdict``, the one the lane route sends with each view), so a hosted
worker starts no probe child Angelo already knows the answer to; without a
well-formed verdict it probes, as before. Reason: the hosted path ran a plain
executor and lacked these (FE native A′, D6, D7);
``test_native_worker_refusals`` compares both paths call by call, and
``test_native_worker_gpu_verdict`` pins the verdict.

**A worker chat hosted in its worker** (design A′, 1.0.6; ``worker_consent`` in the worker,
``worker_consent_lead`` in Angelo; off with ``ANGELO_WORKER_CHAT_HOST=0``). The worker's ``Controller`` applies
every rule above with the same code, but the person's answers and choices stay in Angelo's window, so two things
cross. *Approval digests*: each call awaiting the person is summarized with the digest of its approval binding (its
id, tool, admitted invocation digest, current arguments and expanded program, the scene binding its card shows, the
part's document and its recovery review). Approve carries the digest of the card the person saw and is sent once;
the worker approves only on an exact match, and otherwise ends the call with ``STALE_REVISION`` and nothing run. An
Approve for a call that is not pending, already approved, of a stopped or ended turn, or of a closed chat runs
nothing; Angelo sends none while a Stop is under way or for a card drawn before one. *A consent lease*: Angelo sends
``chat.consent {epoch, grants}`` on open and on every change, with grants computed by the rules above (Auto, Trust
scripts for this chat or the lead's, Always allow scripts, policy folded in; nothing while the chat's connection is
removed or its Team sign-in ended). A chat with no connection record counts as connected exactly when the window would
run it, on its provider's saved key (``connection_available``), so its inherited trust matches a lane chat's. Every
change, widening or narrowing, goes to every hosted chat; a widening is not waited for. Sends run on threads of their
own and may arrive in any order: the epoch, taken on the main thread in the order of the person's changes, decides,
so the worker always holds the newest lease it received. A change made while any ``chat.open`` of the chat is still
unanswered (a deferred open, or a switch's move, which opens inline) goes out once it is (the open carries the lease
registered with it), so a consent never reaches a worker before the chat it is for. Epochs only move forward and the worker refuses an older one. Until a lease is
held, a worker chat runs nothing unasked; each new script grant is a new consent token, so a script queued under a
revoked one asks. A narrowing lease also revokes the unasked calls it no longer covers that have not started. A call starts only
through ``tool_deadlines.started``, under the chat's lock the lease is applied with and only while unrevoked (the one
start-gap rule above, for consent and a turn's end alike): a revocation after its last start check refuses it with
nothing run, and one after its start finds it started, so it runs and its result says what it changed. A
toggle returns at once (no window frame waits on a worker); a revocation the worker has not acknowledged within 2 s
(the UI timer checks) kills the chat's model child, stops its turn and marks the chat as needing approval until the
person's next message re-sends its grants, which that message waits for on its own lane, never the window's frame;
a parked chat's message takes that step only once its relaunched worker reopened and registered the chat, so its
first message is not refused.
Reason: the worker must never act on consent the person withdrew, even when it is wedged. *Approve* sends the digest
each card showed when it was drawn; a card whose call changed since (new arguments, precondition or recovery review)
is not approved, and a script's review is of one (call, digest); every route that approves a hosted card (the
mirror's own ``approve_call`` too) sends only that drawn digest. The worker answers an Approve once the approved edit
or script has run, so Approve and Decline wait as long as any asynchronous op (``ANSWER_SECONDS``), off the window's
frame; no answer is unknown, not a refusal: the card stays approving and the worker's events settle it, and only the
worker's own refusal, or a request never sent, says nothing ran. *Always allow scripts* on a worker chat's card saves
the choice, sends the lease that grants it, and approves that card's script with its drawn digest. *Stop* sends
``chat.stop`` without waiting; a turn not ended 2 s later has its model child killed by Angelo, which owns it. Every
op carries ``lead_seq``, the order the person acted in: a Send still in its lane (its consent acknowledgement
included) is withdrawn by Stop, and the worker refuses a Send ordered before a Stop it already received, so a Stop sent
on its own thread is never overtaken by the Send it stops; such a Send comes back where the person types. Every
``chat.stop`` is ordered whatever sends it (``WorkerProcess.request`` stamps one that has no order), and a worker
records an unordered Stop just after the newest order it received for the chat, so it still refuses what it
overtook (fail closed).

Always allow scripts
====================

The person's persistent, explicit opt-in, MCP's "Always allow" for scripts.
Angelo 1.0 has no script confinement, so it means exactly what the card says:
scripts run with full access to files, the network and other apps on this
computer. It never covers an External conversation (a connected app's), whose
one standing consent is narrower: one script file the person allowed while it
is unchanged (``external_mcp.rst``, "Script files"; consent ``script_file``).

* **Stored** in Angelo's state folder (``always-allow-scripts.json``, off by
  default; a missing or unreadable file is off): per user and install, never in
  a .blend, a chat, options or a transcript. ``always_allow.set_enabled`` writes
  it atomically before the change takes effect, on and off alike, so a crash
  or a forced quit never brings back a choice the person revoked (Blender
  writes its own preferences only at a clean exit, so they don't hold it).
  ``ANGELO_Preferences.always_allow_scripts`` is a view of it (a getter and a
  setter, no stored value), shown in Preferences > AI Providers and the add-on
  preferences. Load Factory Preferences doesn't change it.
* **Who changes it.** The person, in two places only: the **Always allow
  scripts** button on a script's approval card, and the Preferences toggle
  (which also turns it off). The property's setter accepts a change only from
  the Preferences editor while no agent script is executing; any other
  assignment (a script, an add-on, a keymap entry) changes nothing, and the chat
  says "Always allow scripts was not changed: only you can change it, in
  Preferences or on a script's approval card." A script that refers to
  preferences or to Angelo's own add-on (``trusted_scripts.consent_excluded``:
  ``fallback_categories.uses_preferences`` or ``angelo_agent`` in the code)
  never runs under a standing consent, chat trust or Always allow scripts: it
  asks, with the card's preferences warning, and the card offers no Always
  allow scripts for it. This is defence in depth inside one process, not a
  sandbox: approved, unconfined Python that hides those names can still reach
  Angelo's own state (``angelo_execute_python`` has Blender's full privileges).
* **Effect** (``TrustedScripts.script_consent_kind``): only in a chat whose
  effective approval is Auto bounded edits. Ask still asks for every script;
  Read only still rejects them; per-chat trust keeps working and takes
  precedence. Recovery is unchanged: a script while a recovery hold is open is
  refused (``RECOVERY_REQUIRED``), and restore and every other non-script
  approval asks. The card offers the button only for a script in an Auto chat.
* **Consent token** (``always_allow.py``): a new token object each time the
  choice turns on, none while it is off. A script carries the token current at
  admission (after its validation) and runs unasked
  only while that same token is current at dispatch, with the unchanged
  invocation digest (``require_script_authorized``). Turning it off reaches
  the next script, queued ones included; turning it on again never revives an
  old token, and a repeated click on the card's button keeps the current one.
  The card's button saves the choice and stamps that one pending script with
  the current token; the pump then runs it through the same checks.
  ``ui.pump`` follows the policy every tick (``always_allow.sync``).
* **Policy wins.** ``AllowAutoApproval`` off (machine or organization)
  discards the token (scripts admitted earlier never run unasked after the
  policy lifts), hides the card's button and replaces the Preferences toggle
  with "Turned off by your organization."; edits stay in Auto. With
  ``RequireAskForEdits`` the chat is in Ask, where scripts always ask.
* **Receipts.** An always-allowed script's result records
  ``consent="always_allow"`` with ``execution_mode="trusted_unconfined"``,
  ``confined=false``, ``external_effects_undoable=false`` and
  ``stop_interruptible=false``.
* **Benchmarks.** ``eval_live_agent.py --always-allow-scripts`` (bench:
  ``--angelo-always-allow-scripts``) saves the choice in the trial's own state
  folder to measure the opted-in path; ``--python-approvals`` still answers any
  script that asks. A trial reports its mode, and the gate tools refuse to pool
  runs of different modes (``eval_gate.approval_mode``).

Host tests: ``test_native_always_allow.py``. Native: the script card and its
offer in ``test_blender_chat_cards`` and ``test_blender_translation``; the
Preferences toggle's guard needs the compiled app (``test_blender_chat_cards``).
