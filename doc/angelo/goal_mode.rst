..
   SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
   SPDX-License-Identifier: GPL-2.0-or-later

#########
Goal mode
#########

Status: built on ``feature/goal-mode`` (main) and ``feature/goal-mode-1010`` (the 1.0.10 backport), revised after its
design review and the code's break-it review (`Review findings`_); not merged.

A goal is something the person wants worked on *until a condition holds*, across as many agent turns as it takes:
"keep going until it looks like the reference". Angelo holds the goal, measures it after every turn, and when a turn
ends short of it, continues the chat itself with the measurement and where the gaps are. It stops when the check
passes, when nothing measurably improves for a few rounds, when a visible working-time or spend budget runs out,
when the agent stops it with a stated reason, or when the person presses Stop. It works the same on Claude and Codex
because it lives in the host, not in a model's own features.

.. contents::
   :local:


Why
===

Angelo ends a message when the model ends its turn. The host continues on its own in two places only: Auto's single
escalation (``Controller._after_turn``, Claude) and the worker-done event (``worker_resume``). Nothing holds a goal
across turns.

Evidence (one recorded GPT-6.1 run, maximum effort, Auto bounded approvals): the person attached a reference picture
and asked for the work to continue until it matched. After 23 minutes the model ended its turn with a GLB export and
three stills, replying that the result "remains a simplified approximation, not an exact match". No limit, error or
approval ended the turn. Throughout it, the model never compared the scene with the reference, although
``angelo_view`` measures exactly that (`agent_execution.rst <agent_execution.rst>`_, "Free cameras and reference
pictures"). The person had to send a second message. How often the second half (a reference attached, never compared)
occurs across recorded runs is measured before the scenario (`Validation`_); the first half (no owner for work larger
than a turn) is structural.

Two gaps, both the host's:

* **Holding.** A model's turn is the unit of work; a goal larger than one turn has no owner. Re-prompting "keep
  going" is the friction an artist leaving Blender should not meet.
* **Measuring.** The model judged "done" by eye. A held goal with a measurable check turns "does it match yet?" into
  a number both the model and the person see each round.


Principles
==========

* **The person owns the goal; the model owns the work.** The person sets the bar (pass threshold) and the budget, or
  accepts the defaults on the chip. The model may state how the bar is measured (the reference's viewpoint, which only
  it can judge) until the first measured round; after that the measurement is locked unless the person unlocks it. The
  model can end the goal by saying why, and the person sees it.
* **Facts, not workflow.** Each continuation states the measurement, the gaps, the budget used and how the goal ends.
  It never tells the model what to do next.
* **Bounded and visible.** Each automatic round shows its own time and spend; the goal's totals and limits are on its
  chip. An automatic round that reaches the budget is stopped by the host.
* **Same on every runtime.** The goal, measurement, continuation and stop rules are host code shared by both runtimes;
  the model reaches the goal through one deferred Angelo tool, ``angelo_goal``.
* **Never without the person.** A goal starts only from the person's click or the person's own words; never from a
  request Angelo made; never resumes by itself after a restart; and in Ask, a goal recognised from words waits for the
  person's Start.


The goal
========

One goal per chat, and at most one *active* goal per document: while another chat's goal is active on the same
document, a second chat's ``SET``, + menu goal, Send of a chosen goal, Start and Resume are refused with a notice. A
paused or proposed goal elsewhere never blocks (its own Start or Resume is checked when the person presses it), and a
goal that is running is never ended for another chat's. A worker's chat and a connected app's conversation hold no goal
from the chat bar either. Fields:

``objective``
   The person's request it was set from (the draft sent with it, or the agent's restatement when set from words;
   at most 500 characters). Data, never instructions, wherever it is quoted.
``check``
   ``REFERENCE`` (outline match against an attached picture), ``SCENE`` (typed scene checks) or ``JUDGED`` (the
   agent's own judgment). `Checks`_.
``pass_at``
   ``REFERENCE`` only: outline overlap (IoU, 0.50-0.99) at which it passes. Default 0.90. The person's to change.
``measure``
   How it is measured, and whether it is ``locked`` (`Checks`_).
``budget``
   ``minutes`` of agent working time (default 60), ``stall_rounds`` (default 3), ``max_rounds`` (default 20),
   ``unmeasured_rounds`` (default 2) and ``spend``: list-price dollars on a paid connection (API key, team; default
   $10) or tokens on a subscription (default none: a plan's usage is included, and time bounds it). Defaults sit at the
   scale of the project's own one-shot limits for a whole scene build (40-60 minutes, $20-50 list price,
   `oneshot_tests.rst <oneshot_tests.rst>`_, "Limits"): one full job's time, a fraction of its cost.
``origin``, ``status``, ``reason``
   ``chat_bar`` or ``words``; ``pending`` (chosen, not sent; or proposed in Ask), ``active``, ``paused``, or an end
   (`Stopping`_) with its reason.
``connection``
   The connection id and billing mode it started on. A change ends the goal (`Stopping`_), so a goal never moves from
   a plan to a paid key unseen.
``rounds``
   Per round: number, kind (the person's turn, an automatic round, a worker-done turn), working time, tokens, dollars
   (paid), measurement time, the measurement, and its verdict (progress, no progress, unmeasured).

**Storage.** Goals live in ``goals.json`` in the state folder, keyed by chat id, never in the chat file: a chat file's
fields are a whitelist (``SessionStore._fields``), and an older build reads any field it does not know as a chat it
cannot fully read (``sessions.KNOWN_FIELDS``). Same pattern as ``usage.json`` (``usage_meter``): written off the UI
thread, coalesced, bounded (``MAX_CHATS``), a damaged file read as empty and kept beside, and merged with the file
on each write (``usage_meter._merged``'s rule: a goal the file holds that this session never read or removed is kept).
The writer serialises copies taken on the main thread when a goal is saved, never the live records the chat changes,
and every field is checked when read (a malformed record is left out; ``max_rounds`` is at most the rounds kept).
Only the process that hosts the chats writes it: a worker Blender holds no goals. A goal proposed in Ask is stored like an active one, so a restart keeps it proposed. A pending goal chosen in the
chat bar is not stored: it belongs to the composer's draft, like a chosen tool, and returns to the draft if its
message is not sent.

**After a restart** an active goal is ``paused`` with the reason *Angelo restarted* (also one read late from a file
that could not be read at first); its chip offers Resume. It never resumes by itself. An active goal that no round runs
for and none is due for (its chat was opened again in the same session, or a request did not start) is paused the
same way (*nothing was continuing it*), so no goal is ever held with nothing to continue it.


Setting a goal
==============

**From the chat bar.** The + menu's *Keep going until it's done* row (``chat_destinations`` feature ``goal``) puts a
pending goal chip in the composer beside the attachments and chosen tools. A click opens the goal card to choose the
check, pass threshold and budget. On Send the draft becomes the objective and the goal is active with that request; the
connection and its billing are read at that Send (never at the + menu, since the person may switch connection before
sending), and spend defaults follow that billing unless the person chose a spend limit. The check the person chose
stays: the model's ``SET`` with another kind of measurement is refused (``PERSON_CHOSE``), and a chat-bar *Agent
judges* goal takes no measurement.
The request's observation carries the held goal as a state field, ``held_goal`` (objective, check, pass threshold,
limits; never progress, so it is re-sent only when the person changes it), so the model knows from its first round.

**From the person's words.** When a request asks for work to continue until a condition holds, the model may call
``angelo_goal`` with ``action: SET``. Recognising it is the model's, in every language Angelo ships; a host phrase list
would miss most of them and fit a few. In Auto bounded the goal is active at once (as the agent's typed edits run at
once there), with a notice row and its chip. In Ask it is *proposed*: the chip says so and the person's Start makes it
active. Read only holds none.

``SET`` creating a goal is refused in a request Angelo made (a goal round, the worker-done event), in a voice turn,
in a worker's chat (``worker_routing.worker_snapshots``, as ``angelo_workers_start`` checks) and in a connected app's
conversation, whose tool list leaves ``angelo_goal`` out (``external_clients.NEVER_FOR_APPS``).

The tool, ``angelo_goal`` (validated by ``tool_specs.validate_call``; its declaration measured against Codex code
mode's ~2,000-character budget, `gpt_launch.rst <gpt_launch.rst>`_):

``{"action": "SET", "objective": str, "measure"?: Measure}``
   Holds a goal, or sets its measurement while the measurement is not locked. Returns the goal as held, its limits,
   and the measurement taken at once (the baseline).
``{"action": "MEASURE"}``
   Runs the held measurement now and returns it.
``{"action": "STOP", "reason": "DONE" | "NEEDS_INPUT" | "BLOCKED", "message": str}``
   Ends the goal with the agent's reason. On a measured goal ``DONE`` is accepted and shown as what it is: *ended by
   the agent at 88% of 90%*.

``Measure`` is ``{"reference": <attached image id>, "camera": {location, look_at, lens | ortho_scale}, "targets":
"VISIBLE" | {"collection": <name>}}`` or ``{"checks": [{"kind": ...}, ...]}``: check kinds by name, their exact schemas
from ``angelo_agent_capabilities`` as ``angelo_scene_verify``'s deferred checks are, so the declaration stays small.


Checks
======

``REFERENCE``: the outline comparison ``angelo_view`` makes (``view_compare.measure``, ``view_outline``), run by the
host. **Targets are a rule, resolved at every measurement**, never handles: ``VISIBLE`` or one collection by name. A
reference build starts empty and adds parts, and handles die with Undo and deletion, so a fixed handle list would
strand the measurement. ``VISIBLE`` is every visible mesh-like object whose world bounds reach into the camera's view
(a bounds test before any geometry is copied, so off-camera geometry never counts toward the silhouette's triangle
limit). Each result names the objects drawn (up to 32, then a count) with the share of the picture its projected
bounds cover, and notes any object whose bounds cover most of the picture (a floor, a backdrop, a reference image on
a plane): it would inflate the overlap. All of this is in ``SET``'s baseline, so the model can name a collection
instead before the measurement locks.

The host's measurement is a lighter path than the view tool: ``view_framing.free_camera`` for the camera,
``view_compare.tile`` for the reference (decoded once per goal and cached), ``view_silhouette.draw`` for the targets
and ``view_compare.measure`` for the numbers. No PNG is written, no viewport is drawn, nothing enters the file. It
reports overlap (IoU), mean / p95 / maximum edge distance in pixels and world units, centre offset, size ratio, up to
six farthest gaps (each the 3D point it shows and its offset in the view plane), the reference's subject source
(transparency, plain or busy background) and its uncertainty notes. It passes at ``iou >= pass_at``; progress is the
best IoU rising by at least 0.005 (half a point, above one pixel's rasterisation step on a 300-pixel subject).

**Locking.** The first measured round locks the measurement, for every check (a ``REFERENCE`` round needs at least
one target drawn: an object in view that draws no geometry is not counted). A round whose measurement changed is
neither progress nor a stall: changing it never resets the count of rounds without progress. After that ``SET`` with
a measure is not applied: the refusal quotes the current measurement, the agent's proposed one is kept as a request,
and the chip shows *The agent asks to change how this is measured · Allow*; the goal goes on meanwhile. The person's
Allow (or Unlock in the card) applies it. A round whose measurement changed cannot pass. The end is labelled with what was measured: *outline matches 91% from one camera*. What it
does not measure: colour, material, lighting and the views it was not taken from. A goal about those is ``JUDGED``.

``SCENE``: the typed checks of ``angelo_scene_verify``, run by the host. Passes when every check passes; progress is
more checks passing than in any round before.

``JUDGED``: no measurement. It ends when the agent stops it, on the budget, or after ``stall_rounds`` rounds in a row
that changed nothing in the scene. Any edit counts as change, so in practice working time and rounds bound it; the
chip says *agent judges*. A ``REFERENCE`` goal from the chat bar is measured as ``JUDGED`` until the model states its
camera, and its chip says so.

**Unmeasured rounds.** When the measurement cannot be taken (no subject found in the reference, nothing drawn, the
collection gone, a modal tool running), the round is *unmeasured*: neither progress nor stall. Its reason goes into
the continuation and onto the chip. ``unmeasured_rounds`` in a row end the goal (*Could not measure: …*).


Rounds
======

A round is one agent turn under an active goal: the turn it was set in, each automatic round, and a worker-done
turn while it is active. An Auto escalation re-run is part of its round.

1. When a turn's message ends (``Controller._after_turn``'s final branches, beside ``worker_facts.turn_ended``; never
   while Auto re-runs it), ``goal_loop.turn_ended`` records the round's working time and marks the goal due. A turn
   that ended in an error ends the goal (*the turn failed*, with the error). A turn stopped by the person ends it
   (*Stopped*); one stopped at the goal's budget ends it on its budget; any other stop (a document change, a
   connection change) ends it with that reason. The stop's origin is set where Stop is pressed (``ANGELO_OT_stop``)
   and where the budget stops a round, before ``Controller.stop``.
2. The window's update (``ui.pump``, through ``goal_loop.pump``) runs ``goal_loop.step``. A due goal waits while its chat is not idle (no turn,
   nothing pending, no setup owed, Blender not held by a render, bake or job, no modal tool running: the worker-done
   event's conditions), while its scene is not shown, and while the composer holds a draft (the person is typing).
3. The host measures (the round's ``measure_ms``), accounts the round (tokens and dollars from the chat's usage ledger
   as the usage meter counts them, plus the goal's workers' chats; a round's spend is a lower bound by up to one model
   call, which the chip states) and decides (`Stopping`_).
4. If the goal goes on, Angelo sends the next request itself: ``Controller.send`` with ``host_request`` and
   ``host_kind="angelo_goal"``. Its row is ``request_kind: "angelo_goal"``, a host request (``request_rows``): never
   read as the person's words, never opening a worker batch (``worker_resume.started`` checks ``person_request``),
   drawn as Angelo's notice with the round's numbers in the person's language. The model-facing text (English, never
   translated) is facts only::

      From Angelo, not the person: round 3 of the goal held in this chat: "<objective, quoted as data>".
      Measured (outline, camera and objects as set in round 1): overlap 72.4% (passes at 90%; best 72.4%, last
      round 65.1%); edge distance mean 14 px (0.21 m), p95 41 px; farthest gaps: ... Budget: 21 of 60 minutes
      working time, 210k tokens; 0 of 3 rounds without progress. angelo_goal STOP ends the goal with DONE,
      NEEDS_INPUT or BLOCKED and your message.

Only the person's own message (a request with no host request) starts a chat-bar goal, pauses an active one or lets
a proposed one lapse. A request Angelo makes itself (a goal round, the worker-done event, with or without a named
kind) does none of these. *Continue* on a stopped turn (``angelo_continue``) is the person's click on the same work:
it neither pauses the goal nor is stopped by its budget, and counts as the person's round. A steer into a running turn
is the person's words: the goal pauses when that turn ends (the chat's ``person_spoke_at``, set where the person's
Enter lands, is later than the round's start). The person's waiting messages (``controller.inbox``: queued, or paused
after a Stop) hold the next round as a draft does: the person's words go first. These read what the Continue button
(ux-agent-behaviour) and steering and queueing (steer-and-queue) provide; on a build without them nothing sets them.

A message the person sends while a goal is active **pauses** it: their words may change the direction, so Angelo
continues only after Resume on the chip. Two messages never pause: the Send that starts a chat-bar goal, and the turn
in which the model sets one from words. A draft left in the composer holds the next round and the chip says *Waiting
while you write a message*, so a forgotten draft in a background chat is visible. The shown chat's draft is read live
from its editors; a hidden chat's is the draft remembered when it was hidden. A goal proposed in Ask lapses
(*not started*) when the person sends their next message without pressing Start.

Before measuring a round, ``goal_loop`` checks the document: when another file is open (opened, reloaded or reverted)
than the goal's requests ran on, the goal ends (*another file is open*) before anything of that scene is measured. A
turn Angelo stopped ends the goal even when the runtime reports it completed. The one-active-goal-per-document rule is
checked against every chat this process hosts, with the document each works on now, so it holds after a restart. A
defect in Angelo's goal handling ends that goal (*a problem in Angelo ended it*, with the error quoted) and stops a
round of it that runs, so no round runs on without its limits; it is reported once and never stops the window's update
or another chat. A round handed to delivery counts as on its way until its request starts, and a voice session holds
the next round like a running turn.

Before sending a round, ``goal_loop`` checks for a connection change the person chose (``Controller._next_binding``,
which ``send`` would apply first): the goal ends on it instead of sending on the new connection. A request checkpoint is taken per round as for any request (Request
snapshots), which the round's time includes.

Approval modes: Auto bounded continues. Ask continues a goal the person started or confirmed (the chip's Start is the
consent; Ask's edits still ask; an approval nobody answers expires and the working time bounds the round). Read only
holds no goal. A connected app's conversation and a worker's chat hold none.

**Counting spend.** A goal counts from a mark of the usage ledger, moved each time its spend is counted, never from a
per-request snapshot: what the goal's workers use between two rounds counts in the next one. Every round counts,
however it ended (stopped, failed, paused by the agent's ``NEEDS_INPUT`` or ended by its ``STOP``); then the mark
goes, so a later goal counts from its own start. Nothing counts while the goal is paused: the person's own turns and
any worker's usage in that time are theirs, and Resume counts from then.

**Bounding a round.** An automatic round starts only with budget left. While it runs (a goal round or a worker-done
round), ``goal_loop.step`` compares the working time and the spend since the mark plus the request's live usage
(``usage_meter.in_progress``, which the ledger does not hold until the request ends) with what remains; at the limit
it sets the origin and stops the round as Stop does, at safe boundaries with Undo intact. The person's own turns are
never stopped by a goal's budget; their time counts.


Stopping
========

Each end is a status, a notice row in the person's language and the chip's last state until dismissed:

``passed``
   The check passed (with the measurement and what it measured).
``stalled``
   ``stall_rounds`` rounds in a row without progress.
``budget``
   Working time, spend or ``max_rounds`` reached (which one, and the totals).
``unmeasured``
   ``unmeasured_rounds`` rounds in a row could not be measured (why).
``done`` / ``blocked``
   The agent ended it with ``angelo_goal STOP``; its message quoted as data, and the measurement beside it.
   ``NEEDS_INPUT`` does not end it: the goal is *paused* with the agent's question, and the person's answer and Resume
   continue it.
``stopped``
   The person's Stop; or another stop (named), a turn Angelo stopped, a connection or billing change, Read only,
   another file open, or another chat holding this document's goal.
``failed``
   A round ended with an error, Angelo could not send the next round, or a defect in the goal's round (why, quoted
   as data).

``paused`` is not an end: the person's own message, or a restart. Resume continues; Stop ends.


The chip and the card
=====================

The goal chip sits in the composer's chip row, a teal outline with a target glyph, one line, its close glyph Stop goal
(and the running round with it) or, once ended, Dismiss:

* pending: ``Goal · until it matches · 60 min``; proposed (Ask): ``Goal proposed · Start``;
* active: ``Goal · 72% of 90% · round 3 · 21 / 60 min`` (paid: ``· $1.84 / $10``); ``agent judges`` for JUDGED;
* paused: ``Goal paused · Resume``; ended: ``Goal passed · 91% from one camera · 4 rounds · 38 min`` or the reason.

A click opens the goal card (popover kind ``"goal"``, drawn with the kit's controls as the usage card is): the
objective; the check (*Match the attached picture* / *Agent judges*); pass at (80 / 90 / 95%); working time (15 / 30
/ 60 / 120 min); spend (paid: $2 / $10 / $25; plan: off / 1M / 5M tokens); rounds without progress (2 / 3 / 5); what
the measurement is and *Let the agent change it* while locked; the rounds so far (round, kind, time, spend,
measurement, verdict); and Start / Resume / Stop goal. Every string is translatable; all 21 languages and right to
left are checked at two window sizes. The + menu's new row keeps every feature in view without scrolling
(``chat_destinations``' fit test). While another chat holds this document's active goal, the + menu row and the
card's Start and Resume are refused with a notice (*another chat holds this file's goal*).


Codex's own goals feature
=========================

Codex 0.166 ships a stable ``goals`` feature, on by default, which Angelo's launch turns off
(``runtime_launch.codex_command``: ``features.goals=false``). It stays off:

* **It drives turns itself.** When a thread goes idle with an active goal, the app-server starts a new turn with a
  long continuation prompt (``ext/goal`` ``continue_if_idle``). Angelo's approvals, request fences, receipts, scene
  observation and Stop assume Angelo starts each turn, and the turn notifications do not say which turns Codex
  started.
* **Stop is not Stop.** An interrupted turn does not pause the goal in core; a client must also pause it over RPC or
  the next idle continues.
* **Its budget is tokens only, and soft.** No time, no dollars; the limit injects a "wrap up" message.
* **Its stop rules are prompt rules** (about 60 lines of injected instructions), not host measurements.
* **Claude has no counterpart**, so a Codex-only mechanism would make the runtimes differ.
* **Cache.** On, it adds three tools to every thread's prefix whether or not a goal is set. Using ``thread/goal/set``
  only for display is not possible: turning the feature on turns its loop on.

A host test pins ``features.goals=false`` in the launch, so a renamed upstream flag is noticed. The instruction texts
keep saying the runtimes' *native* goal tools are outside the host contract, and name ``angelo_goal`` as Angelo's.


Cost and latency
================

* **Every request:** ``angelo_goal`` is a *deferred* tool on both runtimes, as other occasional families are
  (``plan_kinds.DEFERRED_TOOLS``): its schema is looked up when a goal is asked for, so an ordinary request pays its
  name and one instruction clause only. Measured (``agent_integration.rst``, "Toolset 51"): Claude's plan schema +14 B,
  Claude's instructions +39 B, Codex instructions +52 B, Codex code-mode tools +1,572 B (the deferred declaration in
  the ``more`` namespace, as toolset 50's were); voice threads unchanged. A toolset bump (one cold prefix per thread,
  then cached). The cost of the lookup round falls only on the requests that set a goal. Measured on the gate: first-
  call cache read and cost per task, both runtimes.
* **A request under a goal:** the ``held_goal`` state field (about 0.3 KB, re-sent only when it changes).
* **Each automatic round:** one model request on the warm context (a short host message; the native thread or session
  continues), a request checkpoint, and the host measurement (silhouette draw and NumPy at the reference's size; its
  budget is set by measurement in the native test, expected well under a second). Shown on the round's row.
* **A chat without a goal:** nothing changes: no measurement, rows or timers beyond the window's existing update.


Model-facing changes (for the re-gate)
======================================

1. New deferred tool ``angelo_goal`` (``goal_spec``; 1,551 characters declared), toolset 51.
2. ``codex_text`` and ``claude_text``: the native-goal-tools line adds "angelo_goal holds a goal across turns";
   Codex's deferred families' list names ``angelo_goal``.
3. The observation's ``held_goal`` state field while a goal is held.
4. The goal round's host request text.
5. ``SET`` refusals (host request, voice, worker, other chat's goal on the document, locked measurement, a check the
   person chose: ``PERSON_CHOSE``).
6. An unmeasured round's reason in the continuation is quoted as data (it can name the person's objects).
7. The feature guide's ``goal`` entry and its two examples (``FEATURES`` lookup).
8. Connected apps' tool lists leave ``angelo_goal`` out (``NEVER_FOR_APPS``).


Validation
==========

* **Frequency first:** count, in the recorded one-shot and scenario runs, turns that ended with a reference attached
  and no reference comparison, and turns that ended short of their brief's closed checks; on both runtimes.
* **Host tests, failing first:** the decision table (pass, stall, unmeasured, each budget, each agent stop, Stop and
  its origin, failure, connection change, Read only); storage (round trip, damaged file, restart pauses); the round
  flow on a stand-in runtime (turn ends → measure → host request with ``request_kind: "angelo_goal"`` → next turn; a
  draft holds it; a person's message pauses it; Stop during a round; a round stopped at its budget; an Auto
  escalation is one round; a worker start in a goal round opens no batch); ``SET`` refusals; locking; the tool's
  validation and declared size; the continuation is facts only and quotes the objective as data; ``features.goals``
  stays false; the chip and card states recorded at draw (text within bands, no elision of short strings) in five
  languages and two sizes, and the + menu fit test.
* **Native test:** in Blender, a reference rendered from a known camera of a known shape; the host measurement returns
  the expected overlap, a scene edit toward the shape raises it, an object added later is measured (rule targets),
  and Undo between rounds leaves it measurable.
* **GUI proof in the running app, Director Mode:** the chip pending, proposed, active, paused and ended; the card; the
  round rows.
* **Scenario:** at least three unrelated reference-match briefs held out from development, one non-English brief
  setting the goal from words, one ``JUDGED`` and one ``SCENE`` goal, and briefs that must *not* set a goal. One
  message per side and no directing (`oneshot_tests.rst <oneshot_tests.rst>`_), on Opus 5.5 and GPT 6.1 Sol through
  the harness; Blender + MCP gets the same briefs and images on both models. Both sides are graded by the same grader
  with a fixed grader camera, never the camera Angelo's model chose. Recorded per side: rounds, final overlap at the
  grader camera, stop reason, working time, spend. Pass: each goal reaches its threshold or stops for a stated reason.
* **Gates:** the zero-failure gate sets on Opus 5.5 and GPT 6.1 Sol (evaluation-account profiles); an unasked ``SET``
  there is a defect, fixed in the description and checked on the held-out non-goal briefs, never tuned to the gate's
  tasks.


Assessment
==========

**Scope.** New modules: ``goal_state`` (fields, budget, decision table; no bpy), ``goal_store`` (``goals.json``),
``goal_spec`` (schema, validation), ``goal_measure`` (the host measurement), ``goal_loop`` (request start, turn end,
step, continuation, pause), ``goal_controls`` (the tool, the observation, the person's controls and Stop),
``goal_probes`` (what a goal reads of Blender, the ledger and the chat's settings; tests replace them),
``message_end`` (a message's end: the goal's round, then a worker chat's sample), ``goal_text`` (continuation and
notices), ``chat_goal`` (chip and card). Touched owners: ``controller`` (``send``'s ``host_kind``, ``_after_turn``
through ``message_end``), ``request_rows`` (``goal_loop.goal_round``), ``worker_resume`` (``started``), ``console``
and ``chat_blocks`` (the row), ``turn_context`` (``held_goal``), ``blender_tools`` (the tool's execution),
``tool_specs`` and ``routing.TOOLSET_VERSION``, ``ui`` (update step), ``stop_notice`` (``person_stop``: the chat's
Stop ends a held goal), ``chat_composer`` (``attachment_rows``) and ``chat_view`` (chip, card, actions, beside the
usage card's), ``chat_destinations`` and ``chat_add_nav`` (+ menu), ``codex_text`` / ``claude_text``, ``locale``.
The pinned modules (``controller``, ``ui``, ``chat_view``; ``test_native_module_size``) gain no lines. Not touched: approvals,
staging, Undo, workers' own flow, the runtimes' launch.

**Risks**, each with its mitigation above: runaway cost (defaults, host stop at the budget, consent rules, chip);
a misleading measurement (rule targets, uncertainty notes in every continuation, unmeasured rounds, the label says
one camera); gaming (locking, a changed round cannot pass, the person's threshold); stopping mid-edit (Stop's safe
boundaries); unattended approvals (working time bounds the round; Ask needs Start); prompt cache (one tool, measured);
unasked goals (description, Ask proposal, held-out negative briefs); older builds reading chats (goals outside the
chat file).

**Confidence.** Holding and continuing: 85%. Host measurement on the lighter path with rule targets: 75% until the
native test. Storage and restart: 85%. Recognition from words: 65% until the scenario. Chip and card to the brand bar
on the first build: 60%. Gates at zero failures with the tool added: 70%.


Review findings
===============

The first draft's independent review (2026-11-05) found, and this revision resolves:

* **B1** measurement bound to handles that die as the scene grows → rule targets, resolved per measurement;
  unmeasured rounds.
* **B2** ``state["goal"]`` would not be saved and older builds would flag the chat → ``goals.json``; restart pauses.
* **M1** host-request plumbing assumed the worker event → ``host_kind``; ``angelo_goal`` rows; ``worker_resume.started``
  checks ``person_request``.
* **M2** no stop origin, Auto re-runs → hook in ``_after_turn``'s final branches; origin set by Stop and the budget.
* **M3** the person cannot talk mid-goal → a draft holds the next round; the person's message pauses the goal.
* **M4** gaming through the model's camera and targets → locking, a changed round cannot pass, rule targets, label.
* **M5** billed turns without a click in Ask → a goal from words is proposed in Ask.
* **M6** spend gaps → workers' chats counted, connection change ends it, working time defined, overshoot stated.
* **M7** schema size on Codex → check kinds by name; declared size measured.
* **M8** thin evidence and asymmetric grading → frequency count, held-out briefs, a fixed grader camera.
* Minor: lighter measurement path, ``DONE`` accepted and labelled, JUDGED stall stated, round accounting and the
  checkpoint cost, pending goal with the draft, ``SET`` refusals, + menu fit, held-out negatives for unasked goals,
  one active goal per document, a pin on ``features.goals=false``.

The code's break-it review (2026-11-05; each finding is a failing-first test in ``test_native_goal_breakit.py``,
26 of its 29 tests fail on the code before the fixes) found, and the code now handles as described above:

* **H1** a host request with no named kind was read as the person's message (the worker-done event started a chosen
  goal, paused an active one): whose request it is now follows ``host_request``, never the kind.
* **H2** spend limits ignored the running request's live usage; **H3** workers' usage between rounds was never counted:
  a per-goal ledger mark and ``usage_meter.in_progress``.
* **M1** the draft hold read a snapshot; **M2** another file's scene could be measured, and a turn Angelo stopped that
  reported completed continued; **M3** one goal per document held only within one session; **M4** scene checks never
  locked, a changed measurement reset the stall count, and the model could change the person's chosen check; **M5**
  rounds that ended unusually were not counted; **M6** the connection was read at the + menu; **M7** a defect in a
  round left the goal stuck or failing on every update, and stored records were not fully checked.
* Lows: Stop goal on a paused goal stopped the person's own turn; connected apps were offered the tool; worker-done
  rounds were not budget-bound; the writer copied live records across threads and did not merge with the file; an
  active goal could be held with nothing to continue it; an unmeasured reason reached the model unquoted; objects
  in view that drew nothing were counted as drawn; the guide's examples echoed one test scene.

Its confirmation pass on the fixes found, now fixed with failing-first tests (``ConfirmationTests``,
``StoreProcessesTests``; all 8 fail on the code before): a spend mark left by a goal the agent ended charged the next
goal (N1); opening a chat whose goal a restart paused ended another chat's running goal, and a closed chat's paused goal
blocked the document (N2); with no chat editor shown the remembered draft was ignored (N3); a defect in the budget
check left its round running without limits (N4); a second process wrote its old copies back, resurrecting a dismissed
goal (N5); Resume during a voice session was paused again, and a worker's chat could take a goal from the chat bar
(N6). It also asked that the Continue and steer hooks be stated as not yet called (N7) and that a paused goal's
counting be stated (N8).

The design's confirmation pass found it buildable and added: ``VISIBLE`` limited to the camera's
view, with each object's share and a frame-filling note (N1); a locked measurement's change kept as a request the
person allows (N2); a waiting draft shown on the chip (N3); the connection check before sending (N4); the messages
that never pause (N5); one writer for ``goals.json`` (N6); a proposed goal's life (N7); the one-goal-per-document
rule in the UI (N8).
