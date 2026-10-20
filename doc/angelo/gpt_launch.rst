.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
.. SPDX-License-Identifier: GPL-2.0-or-later

=========================
GPT models through Codex
=========================

How Angelo runs OpenAI's GPT models through the Codex runtime: the model IDs,
how Codex presents Angelo's tools to the model, the prompt prefix Angelo
controls, Codex typed plans and their strict output schema, and the Codex
side of the benchmark harness. The general runtime boundary is in
``codex_runtime_base.rst``; chat threads, forked openings and toolsets are in
``agent_integration.rst``.

Model IDs
=========

As the Codex runtime exposes them on a ChatGPT subscription (``codex-cli
0.160.0``; on macOS the runtime bundled with the ChatGPT desktop app is not on
``PATH`` and ``providers.find_executable`` locates it):

* ``gpt-6-astra`` (display "GPT-6-Astra"), ``visibility: list``;
* ``gpt-6.1-sol`` (display "GPT-6.1-Sol"), ``visibility: list``.

Source: ``codex debug models --bundled`` (offline, isolated ``CODEX_HOME``)
and the Codex models page (https://learn.chatgpt.com/docs/models), which names
both IDs and ``codex --model gpt-6.1-sol``. The picker lists them from the
runtime's ``model/list`` (``chat_settings.normalize_models``; neither is
hidden or slash-namespaced), and ``model_profiles.MODELS`` has both rows. A
team connection has no Codex account catalog, so its models are pinned in
``provider_registry._CODEX_TEAM_MODELS`` with a conservative effort set.

How Codex presents Angelo's tools
=================================

Measured by driving ``codex app-server`` with Angelo's exact launch flags
against a local fake Responses endpoint (``model_provider`` override to
``http://127.0.0.1``), recording each request body. Nothing is sent to a
model, no credentials are used, and ``CODEX_HOME`` is isolated.
``tests/python/angelo_agent/codex_prompt_probe.py`` reproduces these
measurements.

* **Code mode.** Every current GPT model except ``gpt-5.5`` has
  ``tool_mode: code_mode_only``. Angelo's ``features.code_mode=false`` does not
  change that: the model sees one ``exec`` tool (JavaScript) whose description
  declares Angelo's tools as TypeScript, and calls them as
  ``await tools.angelo_*(...)``; they still reach Angelo as ``item/tool/call``.
  Results come back as strings: the first line is the JSON, and a result with
  views adds one ``data:image`` URL per further line. The guidance says so,
  and asks the model to read ``error`` and ``exact_schemas`` whenever present.
* **Declaration budget.** Codex renders each declaration within about 2,000
  characters; past it every union becomes ``unknown``. Without a reference,
  ``angelo_scene_program`` shows ``operations: Array<unknown | ...>`` and
  ``angelo_scene_verify`` shows no check shapes, so the model has no schema for
  programs or checks. Declarations also drop numeric bounds, so the recipe
  tool's description carries every recipe's parameter ranges as one line
  (``action_recipes.parameter_ranges``), and a range error states the range.
* **Prefix per request.** Codex 0.160 sends no top-level ``instructions`` or
  ``tools``: tools are an ``additional_tools`` input item and instructions are
  developer messages. Later requests append only, with ``prompt_cache_key`` =
  the thread ID, so the prefix is byte-stable.
* **Removable.** ``skills.include_instructions=false``,
  ``include_permissions_instructions=false``, ``include_apps_instructions=false``
  and ``include_collaboration_mode_instructions=false`` (undocumented, accepted
  by 0.160.0; a Codex that does not know one ignores it with a warning) remove
  about 6.8 KB. Angelo runs no shell, skills or apps. The multi-agent role and
  collaboration tools stay (the catalog forces ``multi_agent_version: v2``);
  ``[code_mode]`` settings are ignored.
* **No date or time zone.** ``include_environment_context_time=false``
  (codex-cli 0.163, openai/codex#52164; older Codex ignores it) drops
  ``<current_date>`` and ``<timezone>`` from the environment context, as
  Claude's opening has neither. They are the only opening bytes that change
  with the calendar or the machine's time zone: 76 B of a team opening of
  about 108 KB, in the environment-context input item after the tools, the
  instructions and the multi-agent messages. The cache effect is small: a new
  chat on another day than the cached opening matches through the rest of the
  environment context too (390 B) instead of stopping at the date, and a
  thread that outlives a day gets no new environment-context item (Codex's
  own test). Measured on Angelo's 0.163.0 build with ``codex_prompt_probe.py --variant plan --task edit`` (unbilled,
  ``gpt-6-astra``) and ``--config include_environment_context_time=true`` for
  the other arm: the opening is 107,784 B instead of 107,860 B (29,379 B
  instead of 29,436 B at zstd level 3), and the environment context is the
  only input item that differs.
* **Responses that stop early.** codex-cli 0.163 ends a turn at once when a
  response is incomplete (``max_output_tokens``), never retried
  (openai/codex#53451): ``codexErrorInfo`` ``other``, message ``Incomplete
  response returned, reason: <reason>``. The request fails at once, is never
  resent (``codex_retry``), and the chat says "The model stopped before
  finishing its reply (<reason>). Ask again, or ask for a smaller step"
  before Codex's own words (``provider_errors.CODEX_INCOMPLETE``).
* **Deferred families.** The sculpt, Studio and Flow families are declared
  with Codex's ``deferLoading`` (a namespace is required; code mode names them
  ``tools.more__<name>`` and lists them in ``ALL_TOOLS``). A deferred tool
  called from ``exec`` as ``tools.more__angelo_expand_recipe`` reaches Angelo
  as ``item/tool/call`` with ``tool: "angelo_expand_recipe"``,
  ``namespace: "more"``. The code-mode guidance (``providers.CODEX_TOOLS_GUIDANCE``,
  which Claude never reads) names ``tools.more__angelo_job_start`` and says it
  needs no lookup, and says ``image()``'s second argument, if given, is a detail
  string (+90 B). Without the first, a model spends requests looking the
  deferred tool up or searching ``ALL_TOOLS`` for it; without the second,
  ``views.forEach(image)`` passes each index as ``image``'s detail and fails.
  ``image()`` runs in ``codex-code-mode-host``, which the runtime bundles as
  upstream's build (OpenAI-signed on Windows), so the guidance states the
  argument rather than relying on a host that ignores a non-string
  detail.
* **Caching.** OpenAI prompt caching
  (https://developers.openai.com/api/docs/guides/prompt-caching) is automatic
  from 1,024 tokens on GPT-5.6 and later; the whole rendered prefix must match
  (instructions, tool definitions, history), retention is 30 minutes, and tool
  or effort changes invalidate it. Append messages; never rewrite them.
  Caches are per model: Angelo keys no cache, warm opening or prewarmed
  runtime on reasoning effort, so a chat at any effort
  uses the warm opening and cached prefix of its model and prefix
  (``test_native_effort_keys.py``).

Agent runtime and prompt caching
================================

The ChatGPT backend reuses a cached prompt prefix by session affinity, not by
identical bytes, and stock Codex sends each thread's own ID as that routing
key (``prompt_cache_key`` and the ``session-id`` header), so a new chat's first
request (about 24k tokens) was never cached. Angelo bundles a patched Codex
build, its *agent runtime* (``agent_runtime.py``), whose app-server accepts an
experimental ``cacheAffinityKey`` (1-64 visible ASCII characters) on
``thread/start``, ``thread/resume`` and ``thread/fork``; the key replaces the
thread ID as ``prompt_cache_key`` and ``session-id`` and is persisted in the
thread's rollout. Stock Codex ignores the field. Angelo returns to stock Codex
once upstream ships the feature (openai/codex#21796). The build is pinned in
``agent_runtime.json`` (Codex ``0.163.0``, the fork's commit, the
feature and each platform's files); how it is built, checked, bundled, signed
and licensed is in ``releasing.rst``, "Agent runtime".

* **Selection** (``agent_runtime.executable``, asked first by
  ``cli_runtime.find_executable("codex")``):

  1. ``ANGELO_AGENT_RUNTIME`` (only that spelling: the variable is newer than
     the rename, so no ``SENSEI_``/``TOONLAB_`` form selects the program that
     receives the sign-in): the build's ``codex`` executable, for a
     development build that bundles none; ``off`` uses the person's own Codex
     even where the app bundles one (the native suites set it, so their
     stand-in CLIs play Codex; a live A/B against stock Codex sets it for the
     stock arm, and ``eval_live_agent.py`` passes the caller's value through).
     A set but empty value, or one that is not an executable file, is an
     error, never a silent fall back. A release build (its installer carries
     the pin's ``release_marker``, written only by a configure with
     ``ANGELO_REQUIRE_AGENT_RUNTIME``: ``agent_runtime.release_build``)
     accepts only ``off``; a path is refused, saying so. AI Providers shows an
     active value ("Agent runtime override: ANGELO_AGENT_RUNTIME=…").
  2. The bundled runtime: ``Angelo.app/Contents/Helpers/codex`` or
     ``<install folder>\agent-runtime\codex.exe``, found from the running
     executable (``bpy.app.binary_path``), else from the add-on's installed
     place.
  3. Neither: the person's own Codex, found as before (``ai_providers.rst``).

  The agent runtime then runs every Codex connection (ChatGPT, API key, team
  and external providers), ahead of a connection's configured *Executable*,
  which the connection form no longer offers for Codex. ChatGPT sign-in
  (``codex login``) runs through it with the same isolated or reused native
  profile as before (``authentication.native_auth_command``).
* **Pinned and verified.** Every process of the agent runtime must report the
  pinned version exactly in its ``initialize`` user agent (pre-release suffix
  included) and have its code-mode host beside it
  (``agent_runtime.verify``, from ``cli_runtime.note_codex_runtime``); anything
  else is refused before the first experimental request, saying to reinstall.
  It is never newer than gated, so it logs no newer-than-gated line.
* **First run.** Where Angelo carries the runtime, the connect card always
  offers ChatGPT (``first_run.built_in``) and never asks for a Codex CLI to be
  installed; Claude Code is still found as before.
* **Support** (``cache_affinity.check``). The build reports the same version
  as stock Codex, so a shared app-server of the agent runtime is checked once,
  after ``initialize``, in two ``thread/fork`` requests for one random thread
  ID: with an invalid key the answer must be exactly the patched build's
  validation error ("cacheAffinityKey must be 1-64 visible ASCII
  characters"), and with a valid key it must get past validation to "no
  rollout found". Stock Codex ignores the field and answers "no rollout found"
  at once; any other refusal is no support either. Nothing is created or
  billed. Three outcomes are refused (``codex_runtime.Runtime.prepare``), each
  with its own chat error naming the agent runtime and translated where the
  chat shows it (``cache_affinity.CheckFailed``, carried by
  ``Controller._run``'s error row): the process stopped during the check, it
  did not answer within ``PROBE_TIMEOUT`` (10 s; never a bare timeout), or it
  is not the patched build. The person's own Codex is never checked and never
  sent a key.
* **The key** (``cache_affinity.inputs`` and ``derive``,
  ``codex_connect.affinity_key``): ``angelo-`` and 40 hexadecimal digits of a
  SHA-256 over exactly what the chats sharing a cached opening have in common:
  the account, the model (caches are per model), the static prefix and
  ``TOOLSET_VERSION``. The account is the connection mode and
  ``Runtime.account`` (a digest of the account's type and email) under
  HMAC-SHA256 with a per-user secret (``cache_affinity_secret``): 32 random
  bytes created once, owner-only, as ``cache-affinity-secret`` in Angelo's
  per-user data folder (never a per-launch state folder), so every launch and
  trial of one user on one machine shares it. Every other input is public, so
  without the secret anyone who guessed an email could recompute the key; with
  it, nobody outside this user's Angelo can. ``ANGELO_CACHE_KEY_SECRET`` (64
  hexadecimal digits, only that spelling) replaces it, for a campaign that must
  reproduce its keys across machines or users; a folder that cannot be written
  gives a launch its own secret, said on standard error. Reasoning effort is left out: it is a request
  parameter, not prompt bytes (no part of the static prefix depends on it), so
  a key per effort could only route a chat away from a warm cache, and where
  the backend does keep efforts apart a hit fails either way. The same holds
  for the stock-Codex warm base a new chat forks (``codex_connect.variant_key``:
  thread parameters, the turn's model and response format, never its effort;
  its stored openings, the warm at connect and the warm after a chat's first
  turn all use it), so a chat at another effort forks the warm base instead of
  warming its own. The static
  prefix is what Codex sends before the first per-chat content: the base
  instructions, the dynamic tools, the model
  provider, sandbox and approval settings, and the turn's output schema. The
  working directory, date and time zone reach the model only in Codex's
  environment context, after that prefix, so they are not inputs; nor is the
  connection's ID, the profile, the state folder or any process, chat or thread
  identity. Two launches with different state folders, or two connections of
  one account, therefore send the same key for the same prefix, and a different
  prefix gets a different key. The key holds no email, path or connection
  identifier, and outside this user's Angelo it cannot be linked to an
  account; it is a stable pseudonym of (account, model, prefix), the same on
  every request that shares them, so whoever sees those requests (OpenAI) sees
  that they share it. A text chat on a supporting shared app-server sends it on
  ``thread/start`` and never starts a keyless thread there (the key needs only
  the thread's model and the output schema, so an unavailable model or an
  unoffered Fast tier, which its turn reports, still keys it; a mode outside
  ``codex_runtime.ELIGIBLE_MODES`` has no key and is refused there; team
  chats, which never share an app-server, are keyed as in "Team chats" below). ``thread/resume``
  sends none: the runtime keeps the key the thread was started with, persisted
  in its rollout, so a resumed thread stays on the route where its history is
  cached even after a model, effort or tool change. Voice threads are keyless.
  ``ANGELO_CACHE_KEY_TRACE=1`` prints one ``ANGELO_CACHE_KEY`` line per key
  with its model, toolset and prefix digests (never the account's) to the
  app's standard error (a benchmark trial's ``run.log``), to check that trials
  share it. Each request's receipt records ``agent_runtime`` (``bundled``,
  ``override`` or ``own``) and ``cache_affinity`` (``model_routing.rst``,
  "Receipts"). Every chat of one account and prefix shares one route, which
  OpenAI documents overflows above about 15 requests a minute
  (``performance.rst``, "Cache-affinity keys").
* **Team chats** (``codex_team_keys``, ``codex_connect.team_affinity_key``). A
  team chat (``codex_team``) reaches OpenAI through the Angelo gateway with the
  organization's one provider key, so OpenAI's prompt cache is the
  organization's, and every member's chat could read the opening another
  member's chat cached; keyed per thread, none did. Its key is the team's:
  the same derivation with the account input the mode and the organization's
  ID, under HMAC-SHA256 with the team's cache-affinity secret instead of the
  per-user one. The console gives that secret (32 random bytes) to every
  member's app with the team's providers (``ai_providers.rst``, "Team
  connections"), never to a browser session, so a key cannot be computed from
  the organization's ID alone. Members of one team therefore send one key for
  one model, toolset and static prefix; another team, model, toolset, prefix or
  secret gets another, and the member, machine, working directory and effort
  are never inputs. A member removed from the team stops receiving the secret
  (the console refuses their app's token, 401, or answers 404, and the account
  drops it before any refresh), but keeps knowing the
  old one until an administrator rotates it: rotation changes every member's
  key at their next providers read (at most 15 minutes), so for that window
  members on the old and new secrets do not share. That knowledge routes, it
  does not read: OpenAI scopes cached prefixes to the provider organization, so
  a key used from any other organization finds nothing. ``*_SECRET`` product
  variables (this override and ``ANGELO_CACHE_KEY_SECRET``) never reach a
  native child (``runtime_launch.child_environment``). Team chats keep their
  own app-server per chat (the gateway's grant key is fixed when a process
  starts, so they are not in ``ELIGIBLE_MODES``): on the agent runtime the
  process is checked once, as above, before its first keyed thread (two local
  requests; 0.72 ms median on the bundled runtime, n=10), and a failed check
  is refused the same way. The key's derivation takes 1.84 ms median on the
  147 KB team opening (n=30). With no secret (an older console, no seat), on
  the person's own Codex, for voice and on resume, a team thread starts
  without a key, as before. A team thread's receipt records
  ``cache_affinity`` when its connection started it keyed (a thread resumed
  after a reconnect keeps its key in the runtime but is recorded unkeyed).
  ``ANGELO_TEAM_CACHE_KEY_SECRET`` (64 hexadecimal digits, only that
  spelling) replaces the console's secret, also where the console sends none,
  for a measurement against a console that predates it; a team sign-in with a
  seat is still required. Grok, MiniMax and Kimi team connections key
  nothing. Claude team chats need no key: Claude's cache is by identical bytes
  within the organization, which members' openings already are.
* **What precedes the per-chat content.** Across fresh trials, each with its
  own state folder (``gpt-6.1-sol``, low effort, five trials), the rollouts'
  base instructions, dynamic tools and both developer messages are
  byte-identical; only Codex's environment context (the trial's working
  directory) and the chat's first message differ, both after the prefix. A key
  that digested the working directory would differ per trial and route each
  first request away from the cached opening, which is why the working
  directory is not an input.
* **No warm-ups.** A keyed app-server has no warm base threads
  (``Runtime.has_base`` and ``codex_bases.warm`` refuse), so it sends no
  warm-up request, resumes no remembered base and makes no prediction fork
  (``thread/fork`` is used only by the stock path); receipts record no
  ``warm_up`` (``eval_receipts.warm_ups_since`` is 0). Preparing a chat's
  opening still starts and checks the shared app-server off the chat's path.
  Warm bases and forks (``agent_integration.rst``, "Shared Codex app-server and
  forked openings") remain the stock-Codex fallback.
* Tests: ``test_native_team_cache_affinity.py`` (the console secret, its
  lifecycle on the account, team keys and their isolation, keyed team threads
  and the provider path); ``team_cache_key_probe.py`` (unbilled, on the wire:
  each chat a separate member's account, fake console, state folder and agent
  runtime process, with its gateway route on a local recorder. On 2026-10-20
  with the 0.163.0 runtime: two members and a member's second chat sent one
  ``prompt_cache_key`` and ``session-id``; a rotated secret and another team
  each got another; without a secret each chat sent its own thread ID; the
  input before the environment context, the output format and the tools were
  identical in every chat once per-thread item IDs and Codex's per-turn item
  metadata, which are not prompt text, are left out), ``test_native_cache_affinity.py`` (selection, the pin, first run,
  keys, the support check, no warm-ups), ``test_native_agent_runtime_review.py``
  (failure cases: the secret, no-email and API-key accounts, resume, the
  check's failures, selection, receipts, no keyless thread), ``test_native_bundle_contents.py``
  (installing and checking the runtime), ``test_native_sign_notarize_macos.py``
  (its signature), ``test_native_agent_runtime_notices.py`` (its notices);
  ``test_blender_release_features`` on the built app.

Codex chat threads
==================

* ``codex_reference.py``: ``typescript()`` renderer and ``program_reference()``,
  the core action and check kinds as TypeScript, the others by name, in the
  thread's byte-stable instructions (``providers.codex_instructions``).
* ``tool_specs.codex_tools``: deferred families in the ``more`` namespace with
  ``deferLoading``; on OpenAI connections the program's ``operations``,
  ``expected_checks`` and verify's ``checks`` are declared by kind only
  (``CODEX_REFERENCED_ITEMS``), their field comment pointing at the reference.
  External (function-calling) connections keep the exact schemas.
* ``providers.py``: ``codex_command`` adds the four trim overrides; the thread
  uses ``codex_instructions`` (voice keeps ``INSTRUCTIONS``, long identifiers
  and ``dynamic_tools``); tool results are compact JSON in short identifiers;
  ``thread/tokenUsage/updated`` and ``thread/compacted`` are forwarded; a
  reply-less, host-certified edit ends with the host summary; usage-limit
  errors name their limit.
* ``routing.RuntimeContext``: ``short_identifiers`` (no voice, host holds a
  table), ``record_context_tokens``, ``record_context_compaction`` under the
  request fence.
* ``controller.Controller``: Codex text contexts get deltas and an identifier
  table from ``identifiers.CODEX_FIRST_TOOLSET``; earlier Codex threads keep
  long forms. ``record_context_tokens`` feeds ``_turn_round_inputs``; a
  compaction drops the ledger in ``_commit_ledger``; a rejected call that used
  a deferred kind or check gets ``exact_schemas``
  (``plan_schema.deferred_schemas``).
* Tests: ``test_native_codex_launch.py`` (reference, tools, limits, hooks,
  host summary, exact schemas); the Codex cases of ``test_native_cache_diet``
  and ``test_native_id_diet`` pin deltas and short identifiers.

Measured with ``codex_prompt_probe.py`` (no model call, ``gpt-6-astra``,
codex-cli 0.160.0, first request of a thread), before and after the
reference, trims and deferral:

====================================  =========  =========
                                      before     after
====================================  =========  =========
Request                               78,736 B   53,437 B
``additional_tools`` item             51,806 B   31,659 B
``exec`` description                  40,864 B   21,192 B
``unknown`` in declarations           138        28
Developer messages                    4          3
Program shapes visible to the model   none       reference
====================================  =========  =========

Later requests of the same thread append (53,965 and 54,209 B).

Post-commit revisions in Codex turns
====================================

A Claude plan gets a fresh observation each round, but a Codex model calls
tools within one turn, so after a committed edit the identifier table would
still hold the pre-edit revision, and an omitted read set (an inferred
binding) would bind it, failing ``angelo_scene_verify`` with "Verification
target changed since observation". ``tool_content`` therefore records a
committed result's ``live_revisions`` in the table
(``RuntimeContext.observe_result`` → ``context_ledger.observe_committed``);
staged, failed and voice results change nothing. This is the Codex path only.

Failed turns, provider limits and the one resend
================================================

codex app-server ends a turn a provider error stops with ``error``
(``willRetry`` false) and ``turn/completed`` ``failed``, each carrying a
``TurnError`` whose ``codexErrorInfo`` is a name (``"usageLimitExceeded"``)
or an object keyed by its name with the HTTP status
(``{"responseTooManyFailedAttempts": {"httpStatusCode": 429}}``). Codex
retries a failed model request itself only when the server advised a delay
(``Retry-After``: ``error`` with ``willRetry`` true, "Reconnecting... 1/5");
a plain HTTP 429 or an overloaded answer ends the turn at once (one request,
12-73 ms; codex 0.162 against a local endpoint).

* ``provider_errors.codex_error_info`` reads both shapes (never raising);
  ``codex_error_kind`` says ``usage``, ``rate`` (HTTP 429 that is not a usage
  limit) or ``busy`` (``serverOverloaded``, or 502/503/504/529, or 5xx
  answers Codex gave up on). ``codex_error_text`` prefixes the message from
  that classification ("Provider usage limit reached (usageLimitExceeded): …",
  "Too many requests (HTTP 429): …", "Provider overloaded (HTTP 503): …"), so
  the chat's notice card (``notice_kinds``: Usage limit reached, Too many
  requests, Provider is busy) and the harness read the provider's
  classification, not its wording.
* ``CodexProvider``: ``turn/completed`` sets the turn's status and error and
  wakes the request in a ``finally``, so the request ends at once whatever
  else the handler meets; a defect in event handling is printed
  (``pump_guard.report_defect``), never dropped silently by the RPC reader:
  an exception before the wake-up would leave the chat "working" until the
  10-minute idle watchdog.
* One resend (``codex_attempts``, policy in ``codex_retry``): a turn that ended
  ``rate`` or ``busy`` (never a usage limit, ``rateLimitExceeded``, which Codex
  already backs off and retries, or any other error) is sent once more if the
  turn did nothing: no tool call reached the host, no model output of any kind
  streamed, no model response acknowledged the input, and Codex did not retry
  it itself. In practice ``busy`` means ``serverOverloaded``: Codex retries
  5xx answers itself (``error`` with ``willRetry``), which rules the resend
  out. The waits are fixed: a throttle waits 60-62 s (Codex sends no retry of
  a 429 without ``Retry-After``, ChatGPT's websocket error frames carry none,
  and a rolling per-minute request limit has cleared after a minute), an
  overloaded provider 3-5 s. No
  retry hint is read: codex 0.162 never passes the server's text into a
  TurnError it did not retry. The live row counts the wait down ("Provider
  busy — retrying in N s") and Stop stays available: Stop or a newer request
  ends the wait and nothing more is sent, and a Stop that lands just before
  the resend's ``turn/start`` ends the request as stopped (never "Your message
  wasn't sent": Codex holds it). An app-server lost during the wait sends
  nothing more and the request fails with the provider's own error, so the
  "Too many requests" card and the harness's limit evidence stand. One per
  request (plan corrections included), never for voice. Codex records the
  person's message in the thread when the turn starts (``userMessage`` item;
  resending the same input put it in the model's history twice), so the
  resend's only input is a short host note: nothing ran in the failed turn,
  and anything the message above it reports as done (a plan correction lists
  committed operations) still stands. When no such item was seen, the
  original input is sent again. The live row says "Provider busy, retrying…"
  while Codex retries a turn itself. The receipt counts resends
  (``provider_retries_rate``, ``provider_retries_busy``, receipt-only, never in
  the model-read report); the request's time includes the wait, and the
  harness counts a throttled request as a provider limit
  (``eval_provider_limits.angelo_limit``) even when its resend succeeded.
* Not done: answering exhausted 5xx with a sibling ``cacheAffinityKey``
  (``<key>-r1``) so a stable key pinned to a bad route moves. The agent
  runtime's ``thread/resume`` of a loaded thread attaches to it and keeps its
  key (the key is not one of the resume overrides it compares), so a re-key
  needs the thread unloaded and resumed cold through the shared app-server's
  thread bookkeeping (``codex_runtime.Runtime.loaded``), or a runtime change
  that takes the key per turn.
* Happy path: nothing runs until a turn has failed; a notification's own
  handling went from 1.62 to 1.74 µs (median of 7 × 20,000 deltas, reasoning
  deltas and item starts, provider only, UI queue excluded; Apple M4 Pro,
  Blender's Python 3.13), and no request, wait or round trip was added.
* Tests: ``test_native_codex_turn_errors.py`` (the codex 0.162 sequences,
  policy, Stop, a host defect; with ``ANGELO_TEST_CODEX`` set to a codex
  executable, the real app-server against a local endpoint answering 429)
  and the GUI suite ``test_blender_codex_turn_errors.py`` (the stand-in
  app-server's scripted ``rate``/``usage``/``ok`` turns:
  ``fake_provider_cli``'s ``ANGELO_FAKE_CLI_CODEX_TURNS``; the real one-minute
  countdown and Stop, then the remaining cases with a 2 s wait).

Codex typed plans
=================

A Codex model that edits from ``exec`` would otherwise need one more model
round after the host committed and verified the program, only to write the
closing message; a Claude typed plan has no such round. A Codex chat thread on
an OpenAI connection therefore ends every turn with a typed plan under
``turn/start`` ``outputSchema`` (``agent_integration.rst``, "Codex typed
plans"; ``codex_plan`` and ``strict_schema``), and the host writes the closing
message.

Measured with ``codex_prompt_probe.py --task edit`` (no model call,
codex-cli 0.160.0, ``gpt-6-astra``, scripted model, first request of a
thread):

======================================  ==========  ==========
                                        toolset 29  toolset 30
======================================  ==========  ==========
Model requests for a pure edit          2           1
Model requests for a lookup question    2           2
First request                           55,309 B    86,708 B
``text.format`` (strict plan schema)    none        29,764 B
Developer messages                      17,986 B    18,945 B
``exec`` description (recipe ranges)    21,342 B    22,008 B
======================================  ==========  ==========

Codex sends ``outputSchema`` verbatim as ``text.format`` on every request of
the turn and none on a turn without it; OpenAI caches ``text.format`` with the
prompt prefix, so the host sends the same bytes on every turn of a thread and
only a thread's first request reads the schema uncached. A strict output writes
every optional field as ``null``: 2-11 extra fields per edit on the probe's
vectors (+33 B for a recipe, +62 B for a move, +197 B for a material).
External Codex connections (no typed plans) declare the program and verify
tools with ``codex_arguments``: their dynamic tools shrink from 236,890 B to
93,670 B (``angelo_scene_program`` 145,662 B to 28,193 B).

Shorter forms (``strict_shapes``). An everyday object is offered as prefixes
of one property order, so the model may close it after a cut instead of
writing ``null`` for every remaining field: a primitive after its size, a
material after its name, look and ``also``, a program after its operations.
A call of a shaped recipe (``bevel-and-shade``, ``fit-dimensions``,
``material-setup``; ``strict_shapes.RECIPES``) may end after ``mode``, its
parameters may stop after the ones calls usually state (``fit-dimensions``
after ``dimensions``, whose scale bounds then take their defaults), and a
transform after its scale; the other recipes keep their one complete form. Decoding and the host's exact validation are unchanged.
Re-encoding recorded gate turns under the shorter forms, a fit-dimensions
call falls from 84.5 to 44.4 o200k tokens and a turn of any kind from 65.8 to
54.3, for 1,707 B of output schema (``performance.rst``, "Plan output
tokens").

Property order. OpenAI writes a strict output's properties in schema order,
and decoding is constrained by what the schema still allows after each
property. With alphabetically sorted keys a program's first property would be
``expected_checks`` and an operation's ``arguments`` would precede ``tool``;
once a model wrote ``{"operations":[{"arguments":{"mode":``, only recipe
branches remained, so decoding forced a recipe the request did not ask for.
The strict variant therefore keeps the document's order with ``const``
discriminators first and is compacted with ``preserve_order``;
``codex_prompt_probe.py`` records the order Codex forwards (``reply``, then
``operations``; every operation begins with ``tool``; a program with
``mode``). ``test_native_codex_plan.py`` replays recorded final messages:
under a sorted schema only a recipe could follow the ``mode, parameters`` the
model wrote, and the decoder and executor run exactly that literal.

Bindings. Each plan item is bound again just before it runs, past the
revisions the request's own commits replaced (``plan_bindings``), and plan
commits reach the identifier table. Otherwise a plan of [a program applying an
object's scale, a recipe on the same object] would resolve both against the
turn's revision, and the program's commit would make the recipe's binding
stale. A refusal that changed nothing after earlier items committed, or a
stale binding, gets a correction turn with the receipts and a fresh
observation. ``test_native_codex_plan_bindings.py`` replays such plans against
an owner that compares revisions exactly. The plan guidance tells the model so,
and the reference states ``transform_apply``: without either, a model can
spend three requests on such an edit (a schema lookup, the scale applied from
``exec``, then the recipe) instead of one plan (``agent_integration.rst``,
"Codex typed plans").

Limits: the first request of a thread carries about 31 KB more uncached
prompt; OpenAI compiles a new schema once on first use; and a model may still
edit from ``exec`` and then write an empty plan, which saves nothing but does
not regress (an edit repeated in the plan is not run twice).

Platform API schema
===================

The OpenAI platform API validates a strict output schema more strictly than
the ChatGPT backend: it refuses a ``const`` or ``enum`` without a ``type``
("Invalid schema for response_format 'codex_output_schema': In
context=('properties', 'tool'), schema must have a 'type' key",
``invalid_json_schema``), which would fail every edit turn on an API-key or
team route. Every ``const`` and ``enum`` therefore carries its JSON type
(mixed enums become an ``anyOf`` of one typed enum per type).
``strict_schema.strict_problems`` enforces the platform's rules offline and
names this one in the API's words; it reports 82 violations on toolset 31's
schema and none on toolset 32's, signed in to a team or not. The schema grew
from 29,745 to 30,729 B (signed in) and from 29,689 to 30,673 B.

Benchmark harness (Codex)
=========================

``bench_mcp_vs_angelo.py --runtime codex`` runs the same comparison as the
Claude runs with a Codex runtime on both sides:

* MCP side, ``tests/python/angelo_agent/bench_mcp_codex.py``: stock Blender
  with an MCP add-on; ``codex app-server`` with that MCP server as its only
  server (``-c mcp_servers.blender.*``,
  ``default_tools_approval_mode="approve"``), ``approval_policy="never"``,
  ``sandbox_mode="read-only"``, an ephemeral thread, ``CODEX_SQLITE_HOME`` in
  the trial folder, every other Codex default. Codex defers MCP tools: the
  model finds them in code mode's ``ALL_TOOLS`` (as Claude Code's
  ToolSearch). Wall time is ``turn/start`` to ``turn/completed``; requests are
  token-usage updates. Codex starts the MCP server with the thread,
  asynchronously, so on a loaded machine a turn's code could run before the
  server was ready. The first turn therefore waits for the server's ``ready``
  (or ``failed``) status, up to 60 s, inside its measured time, as Claude Code
  connects its MCP servers before the first request
  (``record["mcp_server_wait"]``).
* Angelo side: ``eval_live_agent.py --codex-home`` runs the Codex connection
  with the dedicated profile instead of its own (``reuse_native`` is
  ``~/.codex``).
* One dedicated, signed-in ``CODEX_HOME`` for both sides, named
  ``.../codex-profile/<connection id>`` and never ``~/.codex``; its
  ``config.toml`` may declare no MCP server. Preflight checks only that
  ``auth.json`` exists. To sign it in once::

      CODEX_HOME=<dir>/codex-profile/<connection id> codex \
        -c cli_auth_credentials_store='"file"' login

* Limits: ``eval_provider_limits.codex_limit`` (a failed turn's or ``error``
  notification's ``codexErrorInfo`` naming a limit, HTTP 429 in the error
  that ended the turn, as a Claude turn's 429 counts, ``rateLimitReachedType``,
  or an exhausted window with a failed turn) makes the trial invalid and stops
  the run; a 429 Codex retried through is not a limit. The plan type from
  ``account/rateLimits/updated`` is recorded. On the Angelo side the chat's
  error row ("Too many requests (HTTP 429): …", "Provider usage limit reached
  (…)") is the evidence (``angelo_limit``), as for Claude, at once instead of
  the 300 s task timeout. The task is still graded and its grade recorded;
  the trial is invalid and the run exits with code 3.
* Asymmetry: an overloaded provider (``serverOverloaded``) is not a limit on
  either side, but the sides meet it differently. Angelo resends the turn once
  (``codex_retry``) and its trial stays valid (a pass when the resend
  succeeds); the MCP side's Codex turn fails and is graded as a failure. A
  head-to-head that meets overloads can therefore favour Angelo; the
  receipts' ``provider_retries_busy`` counts say how often it happened.
* ``--dry-run-mcp --runtime codex`` proves the MCP side without a model or
  sign-in: real stock Blender, add-on, MCP server and Codex against a local
  scripted Responses endpoint; the fake model answers the question, then builds
  the cube through ``tools.mcp__blender__execute_blender_code`` from ``exec``.
* Tests: ``test_native_bench_codex.py``.
