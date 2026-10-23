.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Agent Stack: Toolsets 21 and 22
*******************************

What toolsets 21 and 22 changed in the typed-plan contract: named objects in
observation deltas, lookup-only and superseded plans, handle targets,
modifiers in observations, reply-less edits, and corrections after a refusal
at dispatch. It also describes the harness's rate-limit handling and how a
gate run is graded. Fast-tier failure patterns and the frame-change recovery
are in ``fast_tier_reliability.rst``; later toolsets are in
``agent_integration.rst``.

Every safety property is kept: revision staleness and exact revision checks
(inferred bindings are checked exactly like declared ones), approvals (a
corrected plan is approved again), staging and the frame-rebind rules, preview
and Accept, one Undo step per commit.

Named objects, lookups and superseded plans
===========================================

* **Named objects are always in a delta, whole.** A field-level delta of an
  unchanged named object would be ``{"handle": ...}`` or nothing; a changed
  one would lack the name and the values a question needs. The entry is the
  object the person is talking about, so an entry the message mentions is
  always sent whole (changed or not), and every other changed entry keeps the
  field-level delta. A later round of the same turn does not repeat it
  (``events_as_state``).
* **A lookup-only plan always gets the next round** (``verify`` forced on; a
  standalone ``angelo_scene_verify`` still ends the turn, as before).
* **A superseded plan never ends a turn.** Claude Code returns only a turn's
  last ``StructuredOutput`` call, so a turn could hold a lookup call followed
  by ``"Done."`` with no operations. ``providers.superseded_plans`` detects an
  accepted earlier call whose operations the last call drops: nothing runs,
  the untrue reply is not shown, and the model is asked for the whole plan
  (within ``CLAUDE_PLAN_REJECTIONS``). A call the runtime's schema check
  rejected is a retry, never a plan
  (``claude_transport.ClaudeTurn.structured_outputs`` keeps accepted calls
  only).
* **A name where a handle belongs** gets an error that names the fix before
  binding inference (``binding_refusals.not_a_handle``), instead of "declare it
  with the revision from a lookup".

The other fast-tier fixes (frame rebinding, created-name checks, one corrected
plan after a stage failure, clearer reference errors) keep their tests
(``test_native_fast_tier.py``, ``test_native_program_context.py``);
``test_native_agent_stack.py`` pins the ones above.

Toolset 21
==========

``TOOLSET_VERSION`` 21 changed the always-sent guidance: one
``StructuredOutput`` call, the ``{tool, arguments}`` shape and handle-only
targets; reply-less simple edits as the stated default; and the note that a
delta lists named objects whole. The plan schema is unchanged. The appended
instructions grew 189 bytes (budget 10500 → 10600 bytes,
``test_native_plan_schema.py``). Everything earlier stays valid: a Claude
session resumes with one complete message.

Toolset 22
==========

Toolset 22 is 21 plus:

* ``angelo_set_transform`` accepts an object handle. Without this, a model
  following the plan contract (targets are handles) would pass ``"object":
  "o4"`` and the tool would look up an object *named* "o4". A short handle
  the context's table numbered becomes its long handle in the inbound resolver
  (``identifiers._named_objects``), also inside ``angelo_apply_batch``; the
  executor resolves a long handle exactly like a program target
  (``blender_tools.transform_object``: stale or deleted handles fail with the
  scene query's reason). Names and unknown short forms stay names, and Codex
  threads send names as before. A known short handle wins over an object
  literally named like it.
* Observed entries list their ``modifiers`` (name: type, stack order, at most
  8, ``modifiers_omitted`` beyond), wherever an entry already carries its
  dimensions (active, named, changed and revised selected objects;
  ``turn_context._modifiers``). A context fresh after a rebase therefore sees
  an existing Bevel instead of stacking a second one. About 13 tokens per
  named entry with one modifier; the always-sent instructions and plan schema
  are byte-identical to toolset 21.
* ``bevel-and-shade`` refuses an object that already has a Bevel modifier and
  names it, asking for ``modifier_configure`` (a deliberate second bevel stays
  possible with ``modifier_add``).
* A refusal at dispatch, before approval, states ``scene_changed: false``, and
  a cube/location batch so refused, like a program, gets one corrected plan
  (approved again; the limit is ``plan_recovery.FAILED_EDIT_CORRECTIONS``, counted apart from rejected
  plans). Results
  of anything that ran never qualify. The correction prompt says "The host
  stopped your last plan before it changed the scene", which covers both.

Regression tests: ``test_native_toolset22.py``. The stress replay
(``test_blender_program_stress.py``) has a long task set
(``ANGELO_STRESS_TASK_SET=long``, 17 tasks a cycle,
``program_stress_plans.py``): moves by name, ``o<n>`` handle, long handle and
program, and "Change the bevel modifier" decided from the message alone like
a rebased context, including a recipe plan and the correction a refusal asks
for.

Reply-less edits by default
===========================

For a simple edit the model writes no reply, and the person reads Angelo's
verified summary; the model still replies when it has something to say (a
question, a caveat, a choice it made). The guidance says so. No empty chat row
appears: ``Controller`` creates an assistant row only for text, and a plan
whose problem is detected (empty or superseded) emits no text. Two paths end
correctly under this default (``agent_integration.rst``):

* a later round with neither reply nor operations after edits that ran
  (``verify=true``) ends with the host's verified summary of those edits;
* an empty plan anywhere else is asked again within the rejection budget
  (``providers.EMPTY_PLAN``), and only a third one fails.

Token cost
==========

Measured with ``token_accounting.py`` over a recorded 40-task Opus 5.5 run at
toolset 20, using the same tool and tokenizer fit for both toolsets and
rebuilding every recorded message through each toolset's composition
(``replay_turn_context.py`` merges toolset-20 field-level entries;
``RECORDED_PREFIX_FEATURES`` knows toolset 20)::

    python3 tests/python/angelo_agent/token_accounting.py RUN_DIR --toolset 21

=========  ===================================  ======================
Task kind  Cache write per task, t20 → t21      Output per task
=========  ===================================  ======================
move       308 → 409                            128 (unchanged)
resize     374 → 437                            194 (unchanged)
bevel      463 → 509                            238 (unchanged)
material   497 → 573                            228 (unchanged)
question   499 → 576                            120 (unchanged)
create     702 → 769                            294 (unchanged)
=========  ===================================  ======================

The prefix grows 76 tokens per call (cache reads). Nearly all of the write
growth is the whole named entry (about 65 tokens a turn); the accounting
cannot model the lookup rounds the change removes. If the cost matters more
than robustness, a changed named entry could be sent as its changed fields
plus its ``name`` (unchanged ones whole), about 50 tokens a turn less
(``context_ledger.TurnBlocks.observation_value``).

Harness
=======

``eval_provider_limits.py`` classifies provider rate and usage limits (a chat
``error`` row a provider wrote, a runtime result naming a limit, a rejected
``rate_limit_event``, a transcript ``apiError``, a Codex turn that HTTP 429
ended, an Angelo receipt counting a throttled turn it resent
(``provider_retries_rate``, even when the resend succeeded), or a zero-token
turn with a provider's limit message). Only rows that begin as a provider's
limit message count (``PROVIDER_LIMIT_ROW``); host-, plan- and tool-authored
text that names "429" or a limit never does. A Codex chat's error row names
the throttle or limit from the provider's ``codexErrorInfo``
(``gpt_launch.rst``), so Codex and Claude trials are classified alike. The
in-app driver grades the task first and records its grade, then marks it
``status: "invalid"``; it, ``eval_live_agent.py`` and both sides of
``bench_mcp_vs_angelo.py`` mark the trial ``invalid: "rate_limited"``, stop the
run at once with a ``STOPPED`` message (both sides use the same provider account) and exit
with code 3. An overloaded provider is not a limit: Angelo resends once and
stays valid while the MCP side's Codex turn fails (``gpt_launch.rst``,
"Benchmark harness (Codex)"). Invalid trials
are left out of every pass rate, median and curve and counted in
``invalid_trials`` / ``summary.invalid_rate_limited`` and the first lines of
``SUMMARY.md``. ``--summarize-only`` reclassifies runs recorded before this
detection. A rate-limited run is invalid, not a failure; rerun it after the
limit resets.

Harness runs also turn off Claude Code's automatic refusal fallback
(``CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK=1`` in both sides' environments; the
plan prints ``"refusal_fallback": "disabled"``), so a measured run keeps its
pinned model (``model_routing.rst``).

Grading a gate run
==================

The five-task set runs six Angelo tasks per trial (five head-to-head plus the
staged preview), so 5 trials grade 30; ``SUMMARY.md`` reports the
head-to-head count. A gate run passes when every head-to-head task of every
valid trial passed (``Trials with all 5 passed`` 5/5, or 3/3 with 40 tasks),
there are no invalid trials, and ``environment.external_input_suspected`` is
false in every record.

What is counted depends on the gate set's version (``native_coverage.rst``,
"Grading contract"). In version 1, one of the five head-to-head tasks and
eight of the forty are questions graded on the unchanged scene alone:
``SUMMARY.md`` marks them and its ``Graded`` row counts them as ``ungraded``,
so a clean run reads ``passed 20, failed 0, inconclusive 0, ungraded 5,
skipped 0 of 25`` and ``passed 96, failed 0, inconclusive 0, ungraded 24,
skipped 0 of 120``. From version 2 the
questions are read against ground truth, the edits are unchanged, and a clean
run reads passed 25 of 25 and 120 of 120 with nothing ungraded. A result
states its version, and reused Blender MCP trials keep the version they were
recorded with, which ``SUMMARY.md`` says. For finished runs,
``python3 tests/python/angelo_agent/bench_mcp_gate.py RUN_DIR [RUN_DIR ...]``
prints one line each (it only reads the results file) and refuses runs of two
versions unless ``--mixed-versions`` is given.

Limits
======

* ``superseded_plans`` relies on Claude Code reporting a schema-rejected
  ``StructuredOutput`` call as an ``is_error`` tool result; if a runtime
  version reports it otherwise, a retry after a rejection would cost one
  extra round (never a wrong result: nothing of the earlier call runs).
* Forcing the round after a lookup costs one call when a model looks
  something up and has nothing to do afterwards.
* The context rebase remains off by default.
* If a model still looks objects up after an unchanged turn, read the turn's
  message with ``replay_turn_context.py``; the named entry must be in the
  delta. ``replay_fast_tier_cases.py`` replays recorded runs through today's
  host (``fast_tier_reliability.rst``).
