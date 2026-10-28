.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Prompt Audit
************

``scripts/angelo/prompt_audit.py`` checks what each model **received**, read
back from the requests a trial records, never from the source. The per-model
texts are pinned to their bytes (``test_native_per_model_text.py``). That
shows when a text changed, but not whether each route got each fact, and not
what the runtime adds around Angelo's text. A per-model split once left the
Blender 5.2 facts in the Codex text only: Claude never received them, and Opus
wrote ``NISHITA`` in 446 of 975 recorded trials.

.. contents::
   :local:
   :depth: 1

What it reads
=============

* **Claude Code** transcripts (``*transcript*.jsonl``): each distinct
  ``api-request-shape`` line (``shape.system`` blocks and ``shape.tools``),
  the first request's ``api-request-blob`` messages, and the scripts the
  model's tool calls carried.
* **Codex** rollouts (``rollout-*.jsonl``): ``session_meta``'s
  ``base_instructions`` and ``dynamic_tools``, the developer and context
  messages before the first model output, and the turn context. Codex's own
  built-in tools are not in a rollout, so they are not audited.
* A probe's ``requests.jsonl``: a header (the route it exercised, runtime,
  version, source commit), then each request body exactly as sent. A body is
  the truest record: it also holds the runtime's own tools, which a rollout
  leaves out, so a folder with one is read from the body alone.
* The trial's ``<trial>-launch.json`` names the build: the add-on commit, or
  ``addon:<digest>`` when the run copied the add-on without its checkout.

An MCP client lists each server's instructions as a Markdown section of a
context message, so there Angelo's text runs from its opening to the next
heading. Codex in code mode puts no MCP tool or server text in its opening
request: the model finds them at run time in ``exec``'s ``ALL_TOOLS``. The
probe's fake model therefore lists ``ALL_TOOLS`` in its first turn, and what
that listing returns (Angelo's tools and server text) is the route's received
text, marked as retrieved; it also proves the client connected the server.

Routes no trial covers are recorded unbilled by
``tests/python/angelo_agent/prompt_route_probe.py``. It runs Angelo's own
launch code against local fake endpoints that answer "OK": the full Codex
text (a third-party provider through Codex; an OpenAI connection, ChatGPT
sign-in included, reads the deferred text), a voice thread's text requests,
Studio's prompt editor on both runtimes, and Claude Code and Codex as clients
of ``angelo_mcp.py``. A voice session's realtime prompt goes to the realtime
API and is not recorded. ``check_external_mcp_clients.py`` keeps each live
client run's Claude Code transcript and Codex rollout beside its report.

Angelo's text starts at one of the registry's ``openings`` ("You are
Angelo", Studio's prompt editor) and runs to the end of its block: Claude Code
puts Angelo's ``systemPrompt`` (or an ``appendSystemPrompt``) last, after its
billing and identity blocks, and a Codex thread's ``baseInstructions`` is
all Angelo's. Angelo's tools are ``mcp__angelo__*``, the plan schema inside
Claude Code's ``StructuredOutput`` tool, and Codex's ``dynamic_tools``.
Everything else is **foreign text**.

Routes
======

A recording's route comes from what it holds, never from the harness's flags:
the opening, ``StructuredOutput`` or ``mcp__angelo__`` tools on Claude, and on
Codex the program reference, the deferred-family guidance, the plan guidance,
short identifiers and a realtime turn (voice). The routes are listed in
``prompt_registry.json``: ``claude-plan``, ``claude-direct``,
``codex-{full,deferred}-{chat,plan}`` (``-long`` with long identifiers),
``voice``, ``collaborator-{claude,codex}`` (a delegated read-only
collaborator: Angelo's text plus the delegation's own), ``mcp-claude-code``,
``mcp-codex``, ``studio-claude``, ``studio-codex`` and
``parts-check-{claude,codex}`` (Generation Flows' parts check);
``codex-legacy`` is a build older than the program reference. A session that is not Angelo's (the Blender MCP side of a
head-to-head) is skipped, unless a probe recorded it for a route: then every
fact of that route is reported missing.

The fact registry
=================

``scripts/angelo/prompt_registry.json`` states every fact once:

* ``anchors``: strings the received text must all contain, compared with
  whitespace collapsed (the texts wrap a sentence at different columns); a
  list inside ``anchors`` is any-of. Received text is Angelo's text plus
  Angelo's tools, descriptions and schemas included.
* ``routes``: the routes that must receive it (patterns or an alias:
  ``agent``, ``programs``, ``short-ids``, ``mcp``). A recording of such a route
  without it fails the audit, naming the route, the fact and the recording.
* ``review``: routes that do not receive it and whose need is not decided. They
  are reported, never failed. Every route whose model writes scripts receives
  the Blender API facts, so the ``mcp`` routes require the facts of
  ``agent_guidance.BLENDER_API_FACTS``; an MCP recording made before the MCP
  routes stated them fails them.
* ``stale``: patterns for the name a script writes without the fact. They are
  evidence only: the report counts the scripts that wrote one, and the same
  count over the same model's recordings that did receive the fact.

``own_text_only`` names the routes whose model reads only its own text: Studio's
prompt editor (``studio-*``) and the parts check. There, a registered fact meant only for other
routes is a **leak** and fails the audit the same way, whichever fact it is: the
1.0.3 editor on Claude received nine of the chat agent's facts (its plan text)
after its own ~350 bytes. The editor's own text is the fact
``studio.editor_role``.

Three more lists complete it:

* ``foreign_absent``: runtime text or tools Angelo turns off (Claude Code's
  coding-CLI prompt and ``advisor`` tool, Codex's multi-agent text and
  sub-agent tools). One still in a recording of a listed route fails the audit.
  MCP clients are not listed: their runtime is the person's own.
* ``turn_facts``: facts a first user message must carry when its condition
  holds (``when: image``: a message with an attached image).
* ``release_routes``: the routes a release must have a recording of.
* ``reported_routes``: routes the coordinator decided to report without
  failing, each with a ``reason`` and an ``owner``. Every problem of such a
  route still prints and appears in the report, followed by a ``REPORTED, NOT
  FAILED`` line; a registry entry without a reason or owner is refused.

A fact changes when the texts' facts change, never to make a recording pass.

**Per route from source.** ``prompt_audit.py sources`` assembles every route's
Angelo text and tools from a tree (``prompt_sources.py``: each Claude launch
with and without web search, the collaborator, all sixteen Codex variants and
the third-party no-web suffix, voice, the MCP server at each level set, Studio's
editor on each runtime) and applies the same per-route rule as for a recording:
a missing fact or a leak fails, naming the route and its variant. A fact stated
by some other route's text never satisfies it, which is how a per-model split
dropped the Blender 5.2 facts from Claude alone. ``test_native_prompt_audit.py``
runs it, and also refuses a registry fact no current text states, so a reworded
sentence updates its anchors. The registry is the general form of the parity
tests in ``test_native_per_model_text.py``.

The report also lists **unregistered differences**: identifiers one route's
latest recording states and another's lacks, which no fact covers. They are
candidates for the registry, not failures.

Foreign text and digests
========================

Foreign text is grouped by runtime and runtime version (Claude Code's
``version``, Codex's ``cli_version``). The report lists its size, the
runtime's tools and the context injected into the first message, and the
paragraphs added and removed since the previous version, so a runtime update
that changes what the model reads shows as a diff. Email addresses, paths,
user ids, UUIDs and dates are masked in everything written down.

``--digests`` writes, per route and build, the digests of the recorded Angelo
text, Angelo's tools and the foreign text, with recording counts and runtime
versions. ``--baseline`` compares a run with an earlier digests file and
prints every route and build whose digests changed.

Running it
==========

::

    python3 scripts/angelo/prompt_audit.py sources [--tree PATH]
    python3 scripts/angelo/prompt_audit.py check <recordings folder>... \
        [--build COMMIT] [--require-routes] [--report audit.md] [--json rows.json] \
        [--digests digests.json] [--baseline digests.json] [--foreign foreign.txt]

``check`` exits 1 when a recording lacks a fact its route must receive, an
own-text-only route received another route's fact, runtime text Angelo turns
off still arrived, or (``--require-routes``) a release route has no recording
of the build. Both read files only and run no model. In the release chain
(``releasing.rst``), ``sources`` runs on the commit, ``prompt_route_probe.py``
records the routes no gate covers, and ``release_checks.py prompt-audit <gate
campaign> --build <release commit>`` runs ``check --require-routes`` on both.

Route parity for data
=====================

Facts also reach a model as data: fields the host adds to tool results. Each
route reads a result through its own presentation code, and a key list in one
of them stops a field silently: the terse receipt dropped ``quoted_names``, and
a light's ``shines_from`` reached 0 of 18 Claude typed plans (2026-10-21) while
Codex read both. ``scripts/angelo/route_parity.py`` runs every route's real
presentation code (``providers.tool_content`` for a Codex thread,
``DirectRequest._packed`` for Claude's direct tools and both MCP routes, which
reuse it, a Claude plan's verify round, the next message's handoff of a plan's
results with and without the host's summary, and a Codex plan's correction
receipts) over recorded results (``tests/python/angelo_agent/
route_parity_corpus``) and compares them field by field:

* **delete-one**: a key at a location reaches a route when removing it changes
  that route's text. A key that reaches one route but not another fails unless
  ``route_parity.json`` registers the drop for that route, form and tool with a
  reason. Host bookkeeping every model copy leaves out (``model_results``:
  timing, a query's cache, a successful script's consent) needs no entry.
* **probe**: a new key at each location shows where a route keeps only the
  fields it lists. Such a location must be registered as a filter level (``at``),
  so a new field there is a decision, not an accident.
* **budgets**: the handoff keeps a result's first 5,000 characters; what lies
  past them is reported (``budget``), not failed.
* **result facts** (``prompt_registry.json`` ``result_facts`` with ``tools``,
  ``at`` and ``sample``): a fact the host adds for every model is placed last at
  its location in each corpus result of its tools and must reach every route,
  past every filter and budget.

Every compact form keeps a result's facts from one list
(``result_facts.COMMIT_FACTS`` through ``commit_facts``: objects that stopped
sharing data, the World and its Sun lights, motion, unchecked dependents,
unobservable checks and unverified effects, plus an operation's own
``also_slots_only`` lifted as ``operation_facts``): the terse, compact and
unchecked receipts and a Codex plan correction's receipt of any item. A list
longer than 32 rows keeps 32 and states its length (``<key>_count``). The
5,000-character excerpt keeps the result in its own order; when its cut would
lose a fact (a listed key, or an operation's own fact inside
``operation_results``), the prefix gives back its end and the facts follow it
after ``…``, whole and in list order, within 2,500 characters, naming any that
does not fit in ``facts_omitted`` (``bounded_excerpt``). An excerpt the cut
keeps whole, or whose cut loses no fact, reads exactly as before, so no content
moves later than it was; when every fact that would follow is already whole in
the cut as before, that cut stays, less a short marker naming the facts it
loses (exactly as before when the marker would cut a fact that cut kept whole).
It never shows less of a fact than before; a script that printed past the cut
keeps its World.
Each fact is registered in ``result_facts`` with a sample of realistic size,
and a route reaches it only when the sample's words its text lacked without it
appear (handles, which routes shorten, are not compared). Known limits: a moved
frame (``frame_moved``) and unkeyed animated values (``unkeyed_*``) are host
sentences that tell the model what to do, so they stay in the full result only;
task memory's ``historical_native_result`` receipts keep only their own
allowlist (``task_memory._result_summary``).
Open gaps are registered as ``review``
with a note (reported, never failed): the terse receipt drops a light's or
camera's settings and direction, orphan data and a created object's program
alias; a Codex plan's correction
prompt states no committed item's checks, and shows a lookup or a script before
the failed item as its tool name and facts only. A drop, review, level or budget
the corpus no longer exercises is stale and fails, so the registry follows the
code.

The host suite (``test_native_route_parity.py``) checks the corpus; the native
suite ``test_blender_route_parity.py`` checks fresh results from the compiled
app, where a field added after the corpus was recorded first appears, and with
``ANGELO_ROUTE_PARITY_RECORD=<folder>`` (which ``run_native_suites.py`` passes
through) writes them as a corpus file (``route_parity_corpus/native-fresh.json``,
recorded on a 1.0.6 build). ``tests/python/angelo_agent/route_parity_corpus.py``
rebuilds the corpus from recorded chats, shrunk (each list keeps its three rows
holding the most fields and any row holding a field they lack, so the corpus
loses no field a registry entry may name) and scrubbed of paths and names.
``route_parity.py`` prints the open gaps; ``--propose`` prints each unregistered
drop as registry paths for a person to give reasons to.

Turn observations reach every chat runtime through one code path
(``context_ledger.TurnBlocks``); MCP apps send no Angelo turn messages and
read the scene with the scene tools, whose results this check covers.
