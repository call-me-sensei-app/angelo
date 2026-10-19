.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Angelo Model Routing: Provider-Adaptive Design
**********************************************

People bring their own models, so Angelo has to get the lowest cost and
latency at equal quality from whatever account a chat is bound to. This
document describes the routing policy: per-session prompt-cache TTLs, per-model
native contexts, Auto routing behind a measured gate, escalation, fast helpers
under standing consent, capability profiles, and how measured results admit a
model. It states what is implemented and what is still design. Source
references are to ``scripts/addons_core/angelo_agent/`` unless a path says
otherwise.

Auto routing is implemented but inactive: ``model_profiles.MEASURED`` is empty,
and with no measured entries the Default row keeps the connection's default
model (`How to continue`_).

Hard constraints every rule keeps:

* Routing stays inside the chat's connection. Work never moves to another
  account or provider without the person's per-use consent, because billing
  boundaries are the person's (``agent_integration.rst``, "Authentication and
  Billing").
* A model the person picked in the model picker is never switched
  automatically; routing may only suggest one.
* Nothing that may already be live is retried automatically. An escalation
  re-runs a request only when nothing was committed.
* One shared policy over the existing owners (``CONTRIBUTING.md``, "Agent
  Readiness"). ``model_routing.py`` is pure decisions; the controller,
  provider adapters, route keys, handoffs and the context ledger keep
  ownership. There is no second session engine or router process.

Implementation status
=====================

.. list-table::
   :header-rows: 1

   * - Piece
     - State
     - Where
     - Host tests
   * - Native child environment hygiene
     - implemented
     - ``runtime_environment.py``, ``providers.child_environment``
     - ``test_native_session_routing_receipts.py``
   * - Prompt-cache TTL per native session
     - implemented
     - ``Controller.session_cache_ttl``, ``model_routing.cache_ttl``
     - same
   * - Route, served-model, cache-gap and consultation receipts
     - implemented
     - ``telemetry.py``, ``provider_usage.py``, ``delegation.py``
     - same
   * - Published prices and tiers
     - implemented
     - ``model_profiles.py`` (``MODELS``)
     - ``test_native_model_routing.py``
   * - Per-model native contexts (Claude)
     - implemented
     - ``Controller._select_model_context``, ``routing.context_key``
     - ``test_native_auto_routing.py``
   * - Auto routing behind the measured gate (Claude)
     - implemented, **inactive** (``MEASURED`` is empty)
     - ``model_routing.auto_route``, ``Controller._auto_route``
     - same
   * - Latency tolerance in Auto
     - implemented
     - ``model_routing.LATENCY_TOLERANCE``
     - same
   * - Escalation (one re-run, else a suggestion)
     - implemented
     - ``Controller._after_turn``, ``model_routing.next_step``
     - same
   * - Plan confidence flag (toolset 20)
     - implemented
     - ``plan_schema.py``, ``providers.ClaudeProvider.run_request``
     - same
   * - Standing consent for same-account fast helpers
     - implemented, on by default
     - ``Controller.standing_consent``, ``ui.py`` preference
     - same
   * - Refusal fallback off in measurement runs only
     - implemented
     - ``runtime_environment.session_environment``, ``Controller.single_model_runs``
     - ``test_native_session_routing_receipts.py``
   * - Flat-plan terms note and benchmark refusal
     - implemented for any spec with ``plan_terms``; no such spec yet
     - ``provider_registry.plan_terms``, ``ui.py``, ``eval_live_agent.py``
     - ``test_native_eval_live_agent.py``
   * - Harness: TTL, delegation, delegation task set, API-key gate
     - implemented
     - ``eval_live_agent.py``, ``eval_live_tasks.py``, ``bench_mcp_vs_angelo.py``
     - ``test_native_eval_live_agent.py``, ``test_native_bench_mcp_vs_angelo.py``
   * - Measured results to profile entries
     - implemented
     - ``tests/python/angelo_agent/measured_results.py``
     - ``test_native_auto_routing.py``
   * - Auto and per-model contexts for Codex
     - design
     - `How to continue`_
     - –
   * - Host-started helper consultations
     - design
     - `Fast helpers`_
     - –
   * - Connections for flat-rate coding-plan keys
     - design
     - `Subscriptions and plan keys`_
     - –

How it works now
================

Native child environment
------------------------

A Claude Code or Codex child would otherwise inherit everything from the shell
that started Blender. Several variables silently change what a request costs or
which account pays: model aliases (``ANTHROPIC_DEFAULT_*_MODEL``),
``DISABLE_PROMPT_CACHING*``, cache TTLs, Bedrock or Vertex routing, extra
headers or request body, OpenAI organization and project, and the markers of an
enclosing Claude Code session. ``runtime_environment.inherited`` drops these,
grouped as credentials, endpoints, models, caching and context, and sessions.
Names are matched case-insensitively, as on Windows. Proxy, CA-bundle and mTLS
settings always pass (``runtime_environment.NETWORK``, pinned by tests). An
isolated Codex profile also drops ``CODEX_SQLITE_HOME``. When either runtime
documents a new variable of these kinds, add it there with its source.

The one endpoint a child may receive is a team connection's own
(``ai_providers.rst``, "Team account and team connections"):
``providers.child_environment`` sets ``ANTHROPIC_BASE_URL`` for
``claude_team`` only, to the Anthropic route of the app's own configured
gateway origin (``ANGELO_GATEWAY_URL`` through ``team_endpoints``), never from
an inherited variable, and ``ANTHROPIC_API_KEY`` to the grant key. Team Codex
modes get the grant key as ``ANGELO_TEAM_GRANT_KEY`` (an inherited copy under
any spelling is dropped) and the gateway route in their process-local
``model_providers`` entry. Personal connections drop every inherited
endpoint. For routing and receipts a team connection is pay-per-token
(``provider_registry.billing_kind``, billing label *Angelo team*).

Prompt-cache TTL per session
----------------------------

Claude Code writes its main conversation at the 1-hour TTL (2x input) on a
subscription and at 5 minutes (1.25x) on an API key, Bedrock, Vertex or
Foundry. ``CLAUDE_CODE_PROMPT_CACHE_TTL`` overrides it per process. Angelo's
artist chats match that default on every route, so a chat writes the cache as
the same chat would in Claude Code on the same account and costs what it would
cost there. Switching TTL partly misses the cached prefix, so the TTL belongs
to a native session:

* ``Controller.session_cache_ttl`` decides it when a session starts:
  ``model_routing.cache_ttl`` for the chat's ``session_kind`` and billing
  (``provider_registry.billing_kind``), or a harness's
  ``prompt_cache_ttl_override``. An ``interactive`` chat gets 1h on a
  subscription (``claude_subscription``) and 5m on every key-billed route: a
  personal API key (``claude_api``) and a team's gateway key
  (``claude_team``, billed ``API_KEY``). ``autonomous``, ``benchmark`` and
  ``subagent`` get 5m on every route.
* Angelo sets the variable for 5m too, instead of leaving the choice to Claude
  Code, so a new session's TTL is the same whatever the person's machine holds:
  the variable outranks a ``promptCacheTtl`` setting (a managed settings file
  still applies under ``--setting-sources ""``) and ``ENABLE_PROMPT_CACHING_1H``,
  and every inherited TTL variable is dropped (`Native child environment`_),
  including the undocumented ``CLAUDE_CODE_MESSAGES_PROMPT_CACHE_TTL``, which
  in Claude Code 2.1.301 writes the message breakpoints at 5m under a 1h system
  prompt. This precedence is as Claude Code 2.1.293 and 2.1.301 implement it.
* The TTL is recorded in that context's ``runtime_sessions`` entry
  (``cache_ttl``, persisted by ``sessions.py``) and reused by every relaunch
  and ``--resume``. A session that already exists keeps its recorded TTL: an
  API-key or team chat whose session was recorded at 1h (as earlier versions
  of Angelo chose for every interactive chat) keeps 1h, and only its new
  native sessions use 5m.
* A session recorded before TTLs were recorded keeps what Claude Code chose
  then (1h on a subscription, 5m with an API key or a team key).
* ``ClaudeProvider`` sets the variable in the launched process only, and a TTL
  change relaunches the process.
* Consultations use 5m.
* Codex-run providers have no TTL Angelo can set (it is a request field that
  Codex composes), so it is recorded as unset.

``claude_prompt_probe.py OUT --route claude_api|claude_team|claude_subscription``
shows the result without billing anything (Claude Code with a dummy key against
a local fake endpoint, ``performance.rst``). With
``ENABLE_PROMPT_CACHING_1H=1``, ``CLAUDE_CODE_PROMPT_CACHE_TTL=1h``,
``FORCE_PROMPT_CACHING_5M=1`` and ``CLAUDE_CODE_MESSAGES_PROMPT_CACHE_TTL=5m``
inherited, every breakpoint of an interactive API-key or team chat is a plain
``{"type": "ephemeral"}`` (5 minutes) and every one of a subscription chat's
environment carries ``"ttl": "1h"`` (Claude Code 2.1.293 and 2.1.301). The
probe cannot sign in to a subscription; its subscription run keeps the dummy
key and shows the TTL that route's environment sets. As a control, forcing
``CLAUDE_CODE_MESSAGES_PROMPT_CACHE_TTL=5m`` past the filter in that
subscription run leaves the system blocks at 1h and writes the message
breakpoints at 5 minutes, which is why the filter drops it.

The trade-off is cost against gaps. Break-even: with ``w`` the per-turn write,
``P`` the cached prefix, input price ``I`` and read price ``R``, 5 minutes is
cheaper only when the share of turns after an idle gap longer than 5 minutes
stays below ``0.75 w I / (P (1.25 I - R))``. That is ``0.625 w/P`` on Opus
5.5: about 3.5% at a 1.7k write over a 30k prefix and 2% at 1k/30k
(``model_routing.ttl_break_even_share``). The smaller a turn's write relative
to its prefix, the lower the break-even: later turns of a chat typically write
far less than its first request, which also carries the new chat's opening
(``performance.rst``, "Fixed opening of a new chat"). Against the per-turn
saving, one pause of 5 to 60 minutes rewrites the whole prefix at 1.25x, and a
new chat after it rewrites the static prefix that 1h keeps warm across chats.
A key-billed chat with frequent longer pauses therefore costs more at 5m than
it would at 1h; it costs what the same chat costs in Claude Code. The
receipts' ``cache_gap`` buckets show which case real use is.

What a new chat can reuse differs by runtime (``performance.rst``, "Fixed
opening of a new chat"). Claude Code sends the plan schema as its first tool
and Angelo's instructions in the system prompt, and Anthropic's cache matches
by prefix, so a new Claude chat reads the cached opening another chat wrote
within the TTL. Angelo launches it with ``--exclude-dynamic-system-prompt-sections``
(``providers.claude_session``) so the working directory and date no longer
sit in that system block: it stays byte-identical across days and installs.
Stock Codex sends ``prompt_cache_key`` = the thread ID, which no stock
app-server parameter or configuration key sets, so there a new Codex chat's
first request rarely reuses another chat's cached opening, however
identical. Angelo's agent runtime, the patched Codex it bundles, adds one
such parameter, ``cacheAffinityKey`` (``gpt_launch.rst``, "Agent runtime and
prompt caching"): a ChatGPT subscription chat's thread is started with one key
per account, model and static prefix, so every new chat after the first reads
the cached opening (``performance.rst``, "Cache-affinity keys", gives the
measurement and its method), and a resumed thread keeps its key. A model change is a new key, as caches are per model; reasoning effort
is not. On the person's own Codex only an ephemeral ``thread/fork`` in
``experimentalPredictionMode`` keeps its parent's key, and such a chat cannot
be resumed after a restart: ChatGPT subscription chats there fork a warm base
thread of their connection's shared app-server (``agent_integration.rst``,
"Shared Codex app-server and forked openings"); a forked chat continued after
a restart gets its handoff.

Receipts
--------

Each request receipt (``agent_execution.rst``, "Provider usage") records its
route:

* connection mode;
* requested model (empty for the runtime's default);
* the effort actually sent;
* Fast, and the session's TTL;
* a cache-gap bucket: ``new_session``, ``unknown`` after a restart,
  ``under_5m``, ``5m_to_1h`` or ``over_1h``;
* ``auto``, and ``escalation`` (1 on Auto's re-run);
* ``single_model`` in measurement runs, whose refusal fallback is off;
* for Codex, ``agent_runtime`` (which Codex ran it: ``bundled``, ``override``
  for ``ANGELO_AGENT_RUNTIME``, or ``own``) and ``cache_affinity`` (whether its
  thread is keyed; ``codex_connect.stamp_route``);
* the models the runtime reported serving it (Claude Code per model, which
  also exposes its own automatic refusal fallback).

In normal use Claude Code's automatic refusal fallback stays on, and the
served-model list shows when it served a turn. Read-only consultations appear
under ``consultations``, attributed to the request that asked for them, with
their route, served models and usage. Receipts keep only allowlisted
identifiers and numbers. The performance report summarizes routes, cache gaps
and consultations.

Per-model native contexts
-------------------------

Caches are model-scoped on every provider. With one native session per
connection, a model change would resume that session on a model with a cold
cache and rewrite the whole prefix and history: about 28k tokens plus about
1.25k per earlier task.

``Controller._select_model_context`` runs between turns. The base context
under the bare route key belongs to the first model that uses it. Each other
model gets its own context under ``<route key>#<model>``. Switching resumes
that model's own session and sends it only the turns it missed, through the
same bounded handoff and per-context ``synced_length`` cursor a provider switch
uses. The context ledger, TTL, measured size, rebase state and owning model are
all per context. ``invalidate_runtime`` drops every context of a connection.
The provider process is relaunched with ``--resume`` on a switch (about
0.35–0.5 s, ``performance.rst``, "Runtime prewarm"), so no process stays
resident. Codex keeps one thread per connection (see `How to continue`_).

What the escalated model writes:

.. list-table::
   :header-rows: 1

   * - Mechanism
     - What the stronger model writes to its cache
   * - Model switch inside one session (not used)
     - the whole prefix and history (28k + 1.25k per earlier task)
   * - Per-model context, static prefix warm
     - only the handoff of missed turns (typically 3k to 12k tokens)
   * - Per-model context, static prefix cold
     - the 28k prefix plus the handoff

The static prefix (Claude Code's prompt plus Angelo's schema and
instructions, about 28k tokens) is byte-identical across chats of a profile in
one working directory. It stays warm while any chat used that model within the
TTL.

Auto routing and its gate
-------------------------

``Controller._auto_route`` applies only to the Default row of a Claude
connection, without a pinned model, a connection-level model or Fast. It builds
the connection's profile from its own catalog
(``model_profiles.capability_profile``), so a route can only name a model that
account offers, under the catalog's own ID. ``model_routing.auto_route`` then
picks the cheapest model that passes all of these:

* the model and the best model on the connection were each measured on every
  ``AUTO_KINDS`` kind (``scene_edit`` and ``scene_read``), with at least
  ``MIN_TRIALS`` (20) tasks per kind, at the chat's effort;
* its pass rate on each kind is within ``QUALITY_TOLERANCE`` (2 points) of the
  best;
* it is in a cheaper tier than the best model;
* its measured p75 latency on each kind is at most 25% worse than the best
  model's (``LATENCY_TOLERANCE``). A candidate without a measured p75 is not
  eligible.

Otherwise the result is None and Default runs the connection's default model.
Angelo cannot tell an edit from a question before the model answers, so a
model must hold parity on both kinds. Work beyond the measured kinds, such as
planning, reaches a stronger model through escalation. Hysteresis: after two
escalated requests among the last five, the chat holds one tier up (or
returns to Default at the top).

Escalation and plan confidence
------------------------------

Triggers, which apply only to an Auto-routed request
(``Controller._after_turn``):

* ``low_confidence``: the plan sets ``"conf":"low"`` before any operation ran.
  ``run_request`` returns ``escalated`` and nothing runs.
* ``plan_rejected``: ``PlanRejected`` is raised after the two correction rounds
  (``CLAUDE_PLAN_REJECTIONS``).
* ``host_check_failed``: a turn finished ``failed`` with nothing committed, for
  example an expected check that failed on the staged copy.

Transport, sign-in and Stop are never triggers. With nothing committed
(``_turn_mutated`` is set by every direct mutation and native commit), the
request is re-run once on the next tier of the same connection, in that
model's own context. The re-run adds no new user row and reuses the request's
snapshot and observation. The transcript gets a system row, and the receipt of
the re-run records ``escalation`` 1. After a possible live change, a second
trigger or the top tier, Angelo only suggests the stronger model. The
``refusal`` trigger exists in the policy but is not wired: Claude Code's result
does not yet expose a refusal stop reason Angelo can classify.

The confidence flag is an optional, terse plan field, omitted in the normal
case. Its cost, measured without a model call:

* schema: +24 bytes, about 12 tokens at the measured 1.93 bytes per token;
* instructions: +62 bytes, about 21 tokens at 3.0 bytes per token;
* both sit in the cached static prefix, so a call pays a cache read of about
  33 tokens;
* output: 0 tokens in the normal case, and 13 bytes (about 7 tokens) when a
  plan sets it.

The flag is part of toolset 20. Like every toolset change, a toolset bump
makes Codex threads start fresh once with the handoff. Codex has no plan envelope and does
not use the flag.

Fast helpers
------------

``angelo_delegate`` is chosen by the lead model and stays read-only, at
most two per request with 120 s each. Under standing consent
(``Controller.standing_consent``), a consultation runs without the approval
click when all of these hold:

* the person's "Run fast helper models without asking" setting is on (AI
  Providers and the add-on preferences; on by default);
* it uses the chat's own connection and billing mode;
* ``model_profiles`` lists the model as a fast tier.

It is shown in the chat, labelled "standing consent", and recorded with
its usage. Any other account, provider or model asks for approval; nothing
crosses accounts or providers without explicit consent.

When a consultation pays (``model_routing.delegation_value``): the lead spends
a round reading the helper's reply and carries it in its history, so a single
typed edit is never worth delegating. Reads, audits, multi-view checks and
summaries whose bulk would otherwise stay in the lead's long-lived history
are, once they would cost the lead one or more extra rounds. Still design:
starting such consultations from host policy rather than the lead's tool call,
and a stable working directory per connection so consultations reuse their own
prefix cache.

Capability profiles
===================

``model_profiles.py`` owns the published facts. ``MODELS`` holds tier,
runtime, catalog aliases, context window, images, the vendor's published price
table with its source key, and an optional price step (for example a higher
rate above a prompt-size threshold). ``SOURCES`` holds each source's URL and
``READ_ON`` the date the prices were read. ``rates()`` returns the
per-request rates for a TTL and prompt size. ``capability_profile`` builds one
connection's ``model_routing`` profile from its catalog. The host tests check
that every row is sourced and tiered, and one test pins a recorded Claude Code
cost estimate against these rates.
``tests/python/angelo_agent/long_session_cost_model.py`` prices projections
from the same table (``--model``).

``MEASURED`` holds harness results only:
``{canonical model: {effort: {kind: {tasks, pass_rate, p75_ms, cost_usd,
source, date}}}}``. Pass rates describe the model with Angelo's prompts, not
an account, so one run serves every connection whose catalog offers the
model.

Tiers in ``MODELS``:

.. list-table::
   :header-rows: 1

   * - Models
     - Tier
     - Caching
   * - claude-opus-5-5 / claude-sonnet-5-5 / claude-haiku-5-5
     - strong / balanced / fast
     - explicit, per model, 512-token minimum
   * - gpt-6-astra / gpt-6.1-sol / gpt-6-luna
     - strong / balanced / fast
     - automatic, 1,024-token minimum, 30-minute retention
   * - grok-4.6
     - single
     - automatic, keyed by conversation
   * - MiniMax-M3
     - single
     - automatic from 512 tokens, no write charge
   * - kimi-k3
     - single
     - automatic; 5m, or 1h on request

Subscriptions and plan keys
===========================

Angelo runs each provider's unmodified native runtime with the person's own
sign-in: ``claude_subscription`` (Claude Code) and ``codex_subscription``
(Codex with "Sign in with ChatGPT"). Subscription credentials are never
extracted or proxied; a team connection uses the organization's API keys, not
a subscription. Other subscription routes need work that does not exist yet:

* xAI's Grok Build CLI would need a new runtime adapter.
* Flat-rate coding-plan keys (for example Kimi Code or MiniMax M Plan) would
  need a connection spec for their endpoint and model.
* Gemini CLI OAuth from third-party software is against its terms, so Angelo
  does not offer it.

A flat-rate plan connection spec carries ``plan_terms`` (a one-line note and
the plan's terms URL) in ``provider_registry.py``. People may connect their
own plan; AI Providers shows the note under the connection, and the
measurement harness refuses the connection, because such plans are for
personal interactive use. Benchmarks for those providers use their
pay-per-token connections with ``--allow-api-key-connection``.

Every harness run turns off Claude Code's automatic refusal fallback, so a
measurement stays on one model. It sets ``Controller.single_model_runs``,
which ``runtime_environment.session_environment`` turns into
``CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK=1`` for that run's Claude processes,
consultations included; inherited copies are dropped. The MCP side
of ``bench_mcp_vs_angelo.py`` sets the same variable. Receipts mark these
requests ``single_model``.

The harness (``eval_live_agent.py``) accepts both subscription runtimes. It
copies only the selected connection's metadata and never a credential.
Pay-per-token API-key connections (``claude_api``, ``codex_api``,
``grok_api``, ``minimax_api``, ``kimi_api``) need
``--allow-api-key-connection``. They run in place, from a connections
directory dedicated to that one connection, because a key lives in the OS
key-store item of its own connections directory and a copy could not read it.
To make one, start Angelo with ``ANGELO_CONNECTIONS_DIR=<dir>``, add exactly
one API-key connection with Remember on, and quit; pass that directory as
``--source-connections-dir``.

Routing policy reference
========================

Where measurement is expected to move work:

.. list-table::
   :header-rows: 1

   * - Work
     - Expected tier once measured
     - Escalates to
     - Why
   * - Typed scene-program edits (programs, recipes, batches)
     - fast
     - balanced, then strong
     - Exact schemas, host validation and host-certified completion make
       failures visible; one round per edit.
   * - Scene reads and questions
     - fast
     - balanced
     - No mutation, but a wrong answer is a silent loss; graded by answer
       checks in the delegation set.
   * - Planning: multi-object builds, deferred families, ambiguous requests
     - default, at most one tier below
     - strong
     - Not measured by today's task sets; reached through escalation.
   * - Verification
     - host checks, no model
     - –
     - ``verify=true`` rounds stay on the tier that planned the edit.
   * - Vision checks
     - fast, once measured on views
     - balanced
     - Image tokens dominate the round.
   * - Long sessions
     - unchanged
     - –
     - Keep a model's context below its price step with the context rebase
       (``model_routing.rebase_ceiling``).

Per-provider notes
==================

Anthropic through Claude Code
   Claude Code places the cache breakpoints, not Angelo. Keep the inputs Angelo
   controls byte-stable: the ``initialize`` schema and instructions, one
   working directory per profile, and the per-message deltas. Never switch
   model or Fast inside a native session; both are full cache misses. Per-model
   contexts avoid the model switch. Effort changes keep the cache on the 5.5
   models, per Claude Code's documentation.

OpenAI through Codex
   Caching is automatic from 1,024 tokens. On GPT-5.6 and later, writes cost
   1.25x input and reads 0.1x (0.05x on gpt-6.1-sol), so the cache-diet
   arithmetic applies to Codex too. The catalog gives three tiers: luna, sol
   and astra.

Grok (xAI)
   One admitted model, so routing is effort only. Caching is automatic and
   keyed by conversation. Prompts above 200k tokens cost double.

MiniMax
   No cache-write charge and a low read rate, so long prefixes are cheap.
   Effort is on or off.

Kimi
   A 5-minute write costs the input rate and a 1-hour write twice that. The
   vendor's default effort is ``max``; Angelo pins ``high``.

Local llama.cpp
   No token cost, so quality and then latency decide (``billing`` ``LOCAL`` in
   a profile gives cost 0). Keep prefixes byte-stable, and use a fixed
   ``id_slot`` per native context so the KV cache is reused
   (``timings.cache_n`` shows it).

Measuring a model
=================

Commands that send nothing to a model:

* Host tests, with Blender's bundled Python as CI uses:
  ``GIT_LFS_SKIP_SMUDGE=1 <Blender>/Contents/Resources/5.2/python/bin/python3.13 tests/python/angelo_agent/run_host_tests.py``.
* Candidate profile entries from finished runs:
  ``python3 tests/python/angelo_agent/measured_results.py <run dir> [<run dir> ...] --date YYYY-MM-DD``
* Projections at another model's prices:
  ``python3 tests/python/angelo_agent/long_session_cost_model.py --model claude-haiku-5-5 --rebase 90000``
* Every harness command below prints its plan without sending anything when
  given ``--plan-only``.

The harness commands below send real model requests and require
``--authorize-billed-runs``. ``<CONNECTION>`` is the connection's ID in the
profile the harness copies from; ``MODEL`` is a catalog model ID such as
``claude-haiku-5-5``.

Tier measurement, the five-task set (5 trials) and the 40-task set (3
trials), Angelo side only against previously recorded Blender MCP results of
another model (``--cross-model-reuse``: labelled in every report, priced per
side by its own model and never a head-to-head or gate figure;
``measured_results.py`` still reads its Angelo side; the runtime, prompts,
references and test code must match)::

    python3 tests/python/angelo_agent/bench_mcp_vs_angelo.py --authorize-billed-runs --connection-id <CONNECTION> \
        --sides angelo --reuse-mcp-results <mcp 5-task run> --cross-model-reuse --model MODEL --effort low \
        --trials 5 --angelo-observation turn-context --angelo-stable-workspace \
        --output <out>/MODEL-5task
    python3 tests/python/angelo_agent/bench_mcp_vs_angelo.py --authorize-billed-runs --connection-id <CONNECTION> \
        --sides angelo --reuse-mcp-results <mcp long run> --cross-model-reuse --task-set long \
        --long-tasks 40 --trials 3 --model MODEL --effort low --angelo-observation turn-context \
        --angelo-stable-workspace --angelo-context-rebase-tokens 90000 \
        --output <out>/MODEL-long

A 90k rebase keeps Haiku 5.5 below its 100k price step; use the same value for
every model so the runs compare. A run passes when ``head_to_head_pass_rate``
is 1.0 and every task passed, previews included. From gate set version 2
(``native_coverage.rst``, "Grading contract") that includes every question's
answer. Runs of gate set version 1 graded questions on the unchanged scene
alone, so ``measured_results.py`` reports them as ``ungraded``, counts only
graded runs in ``tasks`` and ``pass_rate``, and refuses to combine the two
versions unless ``--mixed-versions`` is given.

Other measurements the harness supports:

* **Effort before tier**: the same commands with ``--effort medium`` or
  ``--effort high``, to see whether higher effort fixes a fast tier's misses
  without a stronger model.
* **TTL comparison**: ``--angelo-cache-ttl 5m`` on the long set, against a
  1h run (a subscription's product default).
* **Helper consultations**: ``eval_live_agent.py --task-set delegation``,
  with and without ``--approve-delegation <model ID>``. The ID must be the
  model's ID in the connection's catalog; a refusal says so before anything
  is sent, and then the alias (for example ``haiku``) is the one to use.
  Compare pass rate, p50/p75 per task, lead rounds and usage including
  ``consultations``::

    python3 tests/python/angelo_agent/eval_live_agent.py --authorize-billed-runs --connection-id <CONNECTION> \
        --model claude-opus-5-5 --effort low --trials 5 --task-set delegation --observation turn-context \
        --approve-delegation claude-haiku-5-5 --stable-state-dir <state dir> \
        --output <out>/opus-deleg-haiku

* **Auto end to end**, once ``MEASURED`` has entries: no ``--model``, so the
  chat uses its Default row. Check ``route.auto``, the served models,
  escalations and pass rate against a run pinned to the strong model::

    python3 tests/python/angelo_agent/eval_live_agent.py --authorize-billed-runs --connection-id <CONNECTION> \
        --effort low --trials 3 --task-set long --observation turn-context \
        --stable-state-dir <state dir> --output <out>/auto-long

* **Pay-per-token providers**: ``eval_live_agent.py`` with
  ``--allow-api-key-connection`` and ``--source-connections-dir <dir>``
  (`Subscriptions and plan keys`_).

How to continue
===============

Admitting a measured model, which is what turns Auto on for a Claude
Default row:

#. Run the tier measurement above on the final code, for the model and for
   the strongest model on the connection, at the effort Auto will route with,
   until each kind has at least ``MIN_TRIALS`` graded tasks.
#. Run ``measured_results.py`` over the runs and review each model: pass rate,
   sample size and which tasks failed.
#. Paste the reviewed entries into ``model_profiles.MEASURED`` with the date,
   and run the host tests.
#. Validate Auto end to end (above), then record the results in
   ``performance.rst``.

Adding a provider's capability profile:

#. Admit the model where its connection is defined
   (``provider_registry.py`` for an HTTP provider; a runtime catalog lists it
   otherwise).
#. Add a ``model_profiles.MODELS`` row: runtime, tier, catalog aliases,
   context window, images, prices with an optional step, and a source key
   whose URL goes in ``SOURCES``. Update ``READ_ON`` when re-reading.
#. Measure on a subscription where one exists, otherwise with
   ``--allow-api-key-connection``: the default and long sets, at the effort
   Auto will route with, until each kind has at least ``MIN_TRIALS`` tasks.
#. Convert with ``measured_results.py``, review, paste into ``MEASURED``, and
   run the host tests.

Not yet implemented:

* **Codex.** Per-model contexts need a ``thread/resume`` on the warm
  app-server when the model changes. Then ``_auto_route`` can accept Codex
  connections and the tiers can be measured.
* **Helpers from host policy.** Starting same-connection fast-tier
  consultations for reads and audits, with a stable consultation working
  directory per connection.
* **Flat-rate keys.** Connection specs for flat-rate coding-plan endpoints in
  ``provider_registry.py``, each with ``plan_terms`` naming its terms page, so
  the note and the benchmark refusal apply.
* **Receipts.** A plan-rejection count, and a refusal classification once
  Claude Code exposes one.

Pitfalls:

* Caches are model-scoped. Never change a native session's model; use
  ``_select_model_context``.
* A session's TTL is fixed when it starts. The recorded ``cache_ttl`` wins over
  today's policy.
* Every new mutation path must set ``Controller._turn_mutated``, or an
  escalation could re-run after a live change.
* A skipped task is not a sample; its failed predecessor already counts.
  Sample sizes are small: at about 120 tasks, one failure moves a pass rate
  by 0.8 points.
* Claude catalogs may list aliases (``haiku``). Profiles map them with
  ``aliases``, and routes use the catalog's own ID.
* Cost estimates on a subscription are plan usage, not money.
* ``bench_mcp_grading`` copies ``eval_live_agent_session.py``'s
  ``bind_checks`` verbatim, so keep its single-object call working.
* Toolset bumps restart every Codex thread once.
* Run host tests with Blender's Python 3.13. Python 3.14 defers annotations
  and hides errors CI would catch, and CI runs on Linux.

Limits
======

* **Silent quality loss.** Host checks catch failed edits, not a weak answer.
  Auto needs parity on both measured kinds, and planning is not measured.
* **Small samples.** The 2-point rule over about 120 tasks cannot separate 99%
  from 97% reliably. Raise ``MIN_TRIALS`` as runs accumulate.
* **Escalation storms.** These are bounded to one re-run per request, with
  hysteresis.
* **Prices drift.** Prices live in ``model_profiles`` with the date they were
  read; re-read the sources before relying on a projection.
* **Contexts diverge.** Each model's context sees a bounded handoff, as a
  provider switch does.
* **Latency.** Auto ranks cheapest first within the quality bar and a 25%
  latency tolerance. A fast tier that needs extra plan rounds can be slower
  on short sessions even when it is faster on long ones.

Sources
=======

* `Anthropic models overview <https://platform.claude.com/docs/en/about-claude/models/overview.md>`__,
  `prompt caching <https://platform.claude.com/docs/en/build-with-claude/prompt-caching.md>`__,
  `effort <https://platform.claude.com/docs/en/build-with-claude/effort>`__,
  `fast mode <https://platform.claude.com/docs/en/build-with-claude/fast-mode>`__
* `Claude Code environment variables <https://code.claude.com/docs/en/env-vars>`__,
  `prompt caching <https://code.claude.com/docs/en/prompt-caching>`__,
  `model configuration <https://code.claude.com/docs/en/model-config>`__,
  `legal and compliance <https://code.claude.com/docs/en/legal-and-compliance>`__
* `Codex environment variables <https://learn.chatgpt.com/docs/config-file/environment-variables>`__,
  `configuration reference <https://learn.chatgpt.com/docs/config-file/config-reference>`__,
  `Codex models <https://learn.chatgpt.com/docs/models>`__,
  `Codex auth <https://learn.chatgpt.com/docs/auth>`__,
  `app-server <https://learn.chatgpt.com/docs/app-server>`__
* `OpenAI prompt caching <https://developers.openai.com/api/docs/guides/prompt-caching>`__
* `xAI prompt caching <https://docs.x.ai/developers/advanced-api-usage/prompt-caching>`__,
  `xAI models <https://docs.x.ai/developers/models>`__,
  `Grok Build docs <https://docs.x.ai/build/overview>`__
* `MiniMax prompt caching <https://platform.minimax.io/docs/api-reference/text-prompt-caching>`__
* `Kimi K3 quickstart <https://platform.kimi.ai/docs/guide/kimi-k3-quickstart.md>`__
* `Gemini CLI terms <https://geminicli.com/docs/resources/tos-privacy/>`__
* `llama.cpp server <https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md>`__

Price sources are listed in ``model_profiles.SOURCES``.
