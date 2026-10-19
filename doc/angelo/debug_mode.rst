.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Debug Mode and Asking Why
*************************

When a trial misses, its closed checks say what is wrong, not why the agent
did it. Two opt-in harness flags make a run answer that question:
``--debug-thinking`` records the model's thinking summaries as the work
happens, and ``--ask-why-on-miss`` asks the agent, in the same chat, three
questions about each failed check. Both are for diagnosis only. A run with
either flag is a diagnostic run: it is never a timing, cost or benchmark
claim, and the tools that turn runs into claims refuse it.

Debug thinking
==============

**What it changes.** Claude Code sends no thinking text in print mode unless
asked. In 2.1.301 its requests carry ``thinking: {type: adaptive, display:
"updates"}`` and every thinking block arrives with an empty text and a
signature.
Debug mode adds ``--thinking-display summarized`` to the launch, which asks
the API for summarized thinking. The flag and that value exist in 2.1.258
(Angelo's floor), 2.1.293 and 2.1.301, the versions checked.

The ``showThinkingSummaries`` setting is not used. Print mode never reads it as
a display request: with it set, Claude Code only drops the ``updates`` display
and sends none (``performance.rst``, "Thinking summaries").

**The switch.** ``Controller.debug_thinking``, False by default. Only a test
harness sets it, in the app, before the chat's first launch
(``eval_diagnostics.configure``). It is not a preference, is never saved and
has no environment variable, so an artist's app cannot turn it on and nothing
outside a harness run carries it. The controller copies it onto each request's
runtime context; ``providers.claude_session`` adds the argument
(``debug_thinking.claude_arguments``), and the session's runtime binding
includes it, so a change relaunches the Claude Code process (resuming its
session) instead of keeping a warm one with the other display.

**On Codex** (GPT 6.1 Sol runs). Angelo's Codex chats ask for reasoning
summaries on every turn (``summary: "auto"``, ``providers``), so the app
changes nothing. The MCP side's ``codex app-server`` then asks for the same on
each ``turn/start`` (``bench_mcp_codex.reasoning_summary``); without the flag
it keeps Codex's own default, which sends none. The summaries land in the MCP
side's raw stream (``codex-stream.jsonl``) and, on Angelo's side, in the chat's
thinking rows and in the rollouts the bench copies beside each Codex trial
(``angelo-trial-NN-rollouts/``, ``copy_codex_rollouts``), which also hold each
request's usage with its reasoning tokens. Every MCP Codex task records
``request_parameters`` (effort, model, the summary asked for and, per request,
its reasoning tokens).

**Where the summaries land.** The existing thinking path, unchanged:
``claude_stream.RoundStream`` turns each thinking block's deltas into
``thinking`` events, the controller keeps them as ``thinking`` rows (the last
16,000 characters of each, redacted like every row), and the next durable
event saves them to the chat's session journal. Claude Code's own session
file, which the bench copies as ``angelo-trial-NN-transcript.jsonl``, keeps
the summary text in each thinking block. On the bench's MCP side the same
argument goes to the client's Claude Code, so both sides run under the same
display, and its raw stream (``claude-stream.jsonl``) keeps the summaries.

**Cost and latency.** Not measured. Summaries are output the API streams, and
whether the display changes the prompt cache was not tested, which is why
debug mode is never used in a timed run.

Asking why after a miss
=======================

**When.** After the trial's last task is graded and, for a scenario or
one-shot set, after its hygiene report and the saved copy that is scored, but
before the Undo probe, which then runs after the follow-up: the agent is asked
with the graded scene in front of it, and the scored file is already written.
Only when a closed check failed. A trial stopped by a provider limit, a failed
sign-in or a startup error is never asked, so no further message is sent to
the provider; a trial its own cost or time cap stopped is asked, with the follow-up's
own time limit and its usage recorded apart. It is one message per trial; the
harness never rephrases or repeats it.

**What it asks** (``eval_ask_why.question``). It opens with a fixed line that
marks it as the harness's diagnostic question, names each failed check with
the values measured in the result (at most twelve, each cut to 400
characters), and asks three questions a person reviewing the result would ask
about each:

#. Which detail of the request or the reference pictures led to this choice?
#. Did it notice, before it finished, that the result did not match what was
   asked?
#. If it did, why did it keep it?

It asks for a short, direct answer without calling tools or changing
anything, and says there is no need to defend the result. It never asks the
model to reproduce or explain its thinking. The reasoning itself comes only
from the thinking summaries that ``--debug-thinking`` records, the display the
provider supports for that.

**How it stays out of the results.**

* Everything graded or scored is finished before the question is sent.
* Angelo's chat is switched to Read only first, so the product refuses every
  mutation, Python included; the driver declines whatever still asks and
  compares the scene digest before and after.
* The question goes through the chat's own ``send``. The one-shot session's
  one-message guard protects the graded message and that session has
  finished; the graded record's message count is unchanged.
* On the MCP side the question goes to the same Claude Code process through
  the stream's own ``send`` and ``collect``, not the one-shot wrappers, so the
  graded message's count, cost watch and stop stay its own. Its raw lines go
  to ``ask-why-stream.jsonl``. The client's MCP tools stay allowed (its
  permissions are fixed at launch), so any tool it calls is listed and the
  scene digest is compared before and after, as on Angelo's side.
* Angelo's transcript copy is split at the question: the graded turns stay in
  ``angelo-trial-NN-transcript.jsonl`` and the follow-up goes to
  ``angelo-trial-NN-why-transcript.jsonl``, so every reader of the transcript
  (steps, plan accounting, replay tools) sees what it saw before.
* The Codex MCP client's thread is not continued; the run plan says so
  (``mcp_ask_why``). Angelo's side is asked on either runtime.

**What is recorded.** ``why.md`` beside the trial's report: Angelo's in
``angelo-trial-NN/trial-01/qa/``, the MCP side's in ``mcp-trial-NN/``. It holds
the failed checks, the exact question, the thinking summaries of the answer
(with debug thinking), the reply, any runtime message, and the facts: outcome,
tools called, approvals declined, whether the scene changed, the model usage,
the seconds and the chat's approval mode. The trial report keeps a short
``ask_why`` entry, or ``{"asked": false, "reason": ...}`` when nothing was
asked.

**What the reply is worth.** It is the model's account after the fact, written
knowing the result: a lead, not proof. Name a cause only when the thinking
summaries or the recorded actions (scripts, read-backs, the order of renders)
show it.

**Provider refusals.** A question that asks for a step-by-step account of the
reasoning behind a choice can be stopped by the provider's safeguards and
refused with category ``reasoning_extraction``, with no answer. The harness
therefore asks the decision-level questions above, which do not ask for the
reasoning. A refusal is still possible and is recorded as the outcome (``refused by the
provider (category)``, with Claude Code's message under "Runtime
messages"). The harness does not reword the question or switch models to
get past it.

MCP request parameters
======================

**What the MCP side records.** Every MCP task record has
``request_parameters`` (``mcp_request_parameters``): the effort and the
requested thinking display from the launch arguments, the display Claude Code
stamped on each request's ``message_start`` line, its version and whether
per-turn effort is active. Angelo's transcripts already keep each request's
parameters (``api-request`` lines). The dry run checks the record against the
request bodies its fake endpoint received.

Marks and refusals
==================

A diagnostic run says so wherever the harness writes:

* the run plan (``RUN-PLAN.json``, the plan in ``bench-mcp-vs-angelo.json`` and
  ``eval-live-agent.json``) and each Angelo trial report carry a
  ``diagnostics`` entry with the flags and the note that it is not a claim;
  an ordinary run's plan has no such entry and is unchanged, so ``--resume``
  of an earlier run still matches;
* ``SUMMARY.md``, ``ONESHOT-REPORT.md`` and ``SCENARIO-REPORT.md`` open with a
  banner that lists the run's ``why.md`` files, the one-shot and scenario
  scorecards get the same ``diagnostics`` entry and the HTML pages a note;
* the transcripts the harness keeps open with an ``angelo-diagnostic-run``
  line (in the raw stream's own line shape, with its own task, on the MCP
  side); readers skip a line type they do not know, and the tests check the
  step and cache readers;
* ``bench_mcp_gate.py`` and ``measured_results.py`` refuse a diagnostic run,
  and ``--mixed-versions`` does not override it
  (``eval_diagnostics.refuse_claims``). The final-run driver
  (``final_runs.py``) accepts neither flag.
* The stamp survives every path that rebuilds or merges results:
  ``--summarize-only`` keeps it from ``RUN-PLAN.json`` in the results JSON,
  ``--reuse-mcp-results`` refuses MCP trials of a diagnostic run, and a
  one-shot group (``ONESHOT-GROUP.md``, ``oneshot-group.json``) carries the
  stamp and the scripts label of its members.

How to diagnose a miss
======================

#. Read the failed check's details and measured values in the trial's report,
   and rule out the grader and the harness first.
#. Read the agent's actual actions in the transcript: the scripts or plans
   that set the failing value, every host read-back that mentions it, and
   where each sits relative to the final render and the end of the turn.
#. Rerun the missed case as a diagnostic run, never as part of a timed one::

       python3 tests/python/angelo_agent/bench_mcp_vs_angelo.py --sides angelo \
           --task-set oneshot-bridge --trials 3 --debug-thinking --ask-why-on-miss \
           --authorize-billed-runs --connection-id UUID --source-connections-dir DIR \
           --angelo-binary PATH --output DIR

   ``eval_live_agent.py`` takes the same two flags.
#. Read the thinking summaries around the step where the failing value was
   chosen and where the read-back arrived. They show what the model weighed,
   which the reply alone cannot.
#. Read ``why.md``: what the agent says led to the choice, whether it
   noticed the mismatch and why it kept it. Cross-check every claim against
   the summaries and the actions. Where they disagree, the actions win.
#. Name the cause only when the evidence shows it, then fix the class of
   problem (host checks, read-back wording, instructions), not the one case.

Validation
==========

``test_native_debug_mode.py`` pins the launch argument and its absence, the
runtime binding, the controller's switch (never saved), a summarized thinking
block reaching a journaled thinking row, the flags, plans, environment and
commands of both harnesses, the marks, that marked transcripts read the same,
and the claim refusals. ``test_native_ask_why.py`` pins which checks are named,
the decision-level question (and that it never asks about the reasoning), the in-app phase (Read only, declines, stop, record), the MCP
side (its own file, a refusal as the outcome) and the transcript split. Both
are unbilled. The unbilled dry run (``--dry-run-scenario``) takes both flags:
``--debug-thinking`` reaches the MCP side's real Claude Code (its argument is
accepted against the fake endpoint), and ``--ask-why-on-miss`` is asked on the
Claude MCP side and in the real Angelo app even though the scripted models
pass every check, and the dry run checks the record and the late Undo probe.
No test checks live that the provider returns summarized text.

``mcp_request_parameters`` and its record are pinned by
``test_native_debug_mode.py`` and ``test_native_bench_review.py``. In the dry
run, debug thinking or a warm-up makes the Angelo side's scripted record a
Claude subscription, so the flag reaches a Claude chat's controller; the
scripted runtime records the Claude Code launch each request would make
(``dry-run-claude-launch.json``, from ``providers.claude_session`` on the
request's own context, never started), and the dry run checks its thinking
display, the trial's stamp, and the MCP side's recorded effort and display
against its request bodies. On ``--runtime codex`` the Angelo side stays a
Codex chat and records the opening its cache-affinity key derives from
(``dry-run-codex-launch.json``).
