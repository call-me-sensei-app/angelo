# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The method and the threats to validity that ``bench_mcp_vs_angelo.py`` writes into every result.

Text only, kept beside the driver so a change of method is one reviewed edit:
``METHOD`` for every run, ``METHOD_QUESTIONS`` from gate set version 2
(``eval_gate``), ``METHOD_CODEX`` for ``--runtime codex``, ``METHOD_SCENARIO``
for the scenario sets, and ``THREATS``.
"""

METHOD = [
    "Same Claude account on both sides: the Angelo connection's native Claude Code profile (CLAUDE_CONFIG_DIR).",
    "Same pinned model, --effort low and Fast off on both sides; the model each side actually used is read back "
    "from Claude Code's own output (MCP: init/result; Angelo: its session transcript).",
    "Same five prompts from eval_live_tasks.build_tasks(), one continuing chat per trial, factory default-cube scene.",
    "MCP side: stock Blender with a private copy of Blender Lab's MCP add-on (default 0.25 s active / 1 s idle socket "
    "polling), Blender Lab's installed MCP server, headless Claude Code with its default system prompt and built-in "
    "tools; --allowedTools mcp__blender (all of that server's tools), --permission-prompts none (any other prompt "
    "is denied and reported), --strict-mcp-config, --no-session-persistence.",
    "Angelo side: the unchanged live-eval harness (approvals on Ask; its test-only rule approves typed bounded scene "
    "tools and declines reviewed Python, file writes and the rest).",
    "Wall time = prompt sent -> final result (MCP: Claude Code's result line; Angelo: the request receipt's total_ms). "
    "The first prompt of each trial includes the runtime launch on both sides: by default Angelo readies nothing "
    "before it (ANGELO_EVAL_OPENING_WAIT 0; each trial's report records its opening).",
    "Before every MCP prompt the driver waits 6 s after its own last add-on request, so the add-on is on its default "
    "idle polling, as after a human pause; Angelo sends its next prompt immediately (it does not poll).",
    "Correctness: after each turn the driver (not the model) grades the scene with the Angelo harness's own "
    "predicates (verify() + eval_live_tasks checks + the session's scene_digest/bind_checks/smooth_shaded); MCP "
    "grading runs over the add-on socket. Failed checks count as failures; their time is still reported.",
    "Both sides start from a minimal environment (PATH, HOME, USER, locale; DISABLE_AUTOUPDATER=1) so no "
    "inherited ANTHROPIC_*/CLAUDE_CODE_* routing or session variable reaches either runtime.",
    "Trials alternate MCP, Angelo, MCP, Angelo...; each trial is a fresh process pair, scene and session; "
    "nothing is retried.",
]

THREATS = [
    "Safety asymmetry: the MCP side executes model-written raw Python in the live scene with no staging, "
    "verification gate, approval or single-step Undo; Angelo stages typed operations, verifies and commits one Undo "
    "step. Equal latency would not mean equal risk, and Angelo's checks cost time the MCP side does not spend.",
    "Prompt wording: the prompts were written for Angelo's harness (explicit names, exact numbers). Other phrasings, "
    "vaguer requests or larger scenes may favour either side; five short tasks are not a general workload.",
    "Small sample: n = 5 trials per side and task; medians and p75 are reported with min/max, but differences of a "
    "few seconds are within run-to-run spread. No significance test is claimed.",
    "Network and service variance: both sides call the same hosted model over the internet; queueing, retries and "
    "time-of-day load move wall times. Alternation spreads drift across sides but does not remove it.",
    "Shared machine: other agents' builds/tests ran concurrently; load averages are recorded per trial.",
    "Context asymmetry by design: Angelo attaches a bounded scene observation to each prompt; the MCP side must call "
    "tools to see the scene. Angelo's Claude Code runs with a restricted tool set and structured output, the MCP "
    "side with Claude Code's full default tool set and system prompt. This is each product as shipped.",
    "Stock Blender (MCP; its version and build hash are under stock_blender) vs the Angelo 5.2.2 build: different "
    "binaries; Blender-side execution is milliseconds on both, so this does not drive end-to-end time, but window "
    "focus behaviour differs (stock Blender may come to the front).",
    "The 6 s settle and the add-on's 1 s idle polling are the MCP add-on's defaults; a user who lowers the idle "
    "interval would see somewhat faster MCP tool round trips.",
    "Cost figures are Claude Code's own estimates, not a bill; this is a subscription account.",
    "Prompt cache: the MCP side reuses one empty working directory, so after its first trial its system prompt is "
    "served from the prompt cache. Each Angelo trial has a new workspace path (Claude's working directory) unless "
    "the run used --angelo-stable-workspace (see plan.angelo); see the first-message cache writes under "
    "Observations. Without it this mainly lowers the MCP cost estimate.",
    "Round-count variance: Angelo's number of model rounds depends on the model's own verify choice per plan, and "
    "Claude Code defers MCP tools behind ToolSearch on first use; both are counted as they happened, and n = 5 may "
    "under-sample how often each occurs.",
    "Wall-clock boundaries differ slightly: the MCP clock ends when the driver reads Claude Code's result line; "
    "Angelo's receipt total_ms ends inside the controller (the harness's send-to-idle time is also recorded).",
]


# Only with --angelo-opening-wait above 0: a labelled extra, never the primary cold-start comparison.
METHOD_OPENING = [
    "Extra variant, opening ready before the first message: when Angelo's chat is bound to its connection, as "
    "when its model catalog loads in the app, its opening is readied (the connection's shared Codex app-server "
    "started, its base resumed or warmed) and the first prompt waits for it up to --angelo-opening-wait seconds. "
    "That wait is in no task's time; a first task whose shared runtime already ran at its send is 'prestarted' "
    "(also without the wait, when the app started one on a catalog load); a warm-up it sent is billed "
    "and priced into the task during which it was recorded.",
]

THREATS_OPENING = [
    "Opening asymmetry (extra variant only): Angelo's first prompt starts with its runtime already launched and "
    "its opening readied, while the MCP side's first prompt still includes starting and initializing its Codex "
    "app-server; Angelo's first-task time is not comparable to the MCP side's cold start.",
]


METHOD_QUESTIONS = [
    "Questions (gate set version 2; a side whose trials are reused from a version-1 run is named above and was "
    "not graded this way): each question keeps its wording and ends with one sentence that asks for an answer "
    "line. The driver, never the model, computes the truth from the scene before it sends the prompt and reads "
    "the turn's answer line against it (eval_answers); a question passes only on the right answer with the scene "
    "unchanged. So that neither answer can be given without reading the scene, the driver adds one small mesh "
    "before the opening question and moves the cube by a fixed offset before each later one, and undoes both "
    "after grading. An answer read with a liberty (units among the numbers, a count before the names) is counted "
    "as parsed leniently.",
]


METHOD_CODEX = [
    "Runtime codex: the MCP side is codex app-server (the binary Angelo's Codex connections run) with Blender Lab's "
    "MCP server as its only MCP server (-c mcp_servers.blender.*, default_tools_approval_mode=\"approve\"), "
    "approval_policy=\"never\", sandbox_mode=\"read-only\", an ephemeral thread and every other Codex default; "
    "Angelo's side is its Codex connection. Both use one dedicated signed-in CODEX_HOME (same ChatGPT account).",
    "Codex defers MCP tools: the model finds them in code mode's ALL_TOOLS before its first call, as Claude Code "
    "defers them behind ToolSearch. Wall time = turn/start -> turn/completed; requests = token-usage updates.",
    "Cost: a ChatGPT subscription reports tokens, not money; cost_usd is not recorded for either side.",
]


METHOD_SCENARIO = [
    "Scenario set: one continuing chat of scripted turns with the same prompts and byte-identical reference "
    "images on both sides (Angelo: chat attachments; Claude Code: base64 image blocks; Codex: local images); "
    "each turn is graded by closed host checks (scenario_checks) and the scene is digested after it.",
    "Scenario approvals: Angelo's tester also approves reviewed Python (--python-approvals approve; review, "
    "then approve, as a person clicks twice) and counts each approval; the MCP side has no approval gate.",
    "At the end both sides save a copy (no thumbnail), report scene hygiene and run the Undo probe; the saved "
    "scene is captured and scored against the ground truth in stock Blender (scenario-scorecard.json, "
    "SCENARIO-REPORT.md, scenario-report.html).",
]
