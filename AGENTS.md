<!-- SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd. -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Angelo Repository Instructions

This is the Angelo repository, built directly on Blender. This file is
the authoritative coding-agent entry point for **this Git checkout**. Paths below
are relative to its root.

Read [CONTRIBUTING.md](CONTRIBUTING.md#engineering-standards) before changing
anything. It contains the shared human-maintainability, performance,
agent-readiness, pre-change assessment, confidence, and validation rules.
[CLAUDE.md](CLAUDE.md) is only a compatibility entry point to these same rules.

## Working Contract

- Humans must be able to understand, debug, test, and maintain the code without
  AI. Prefer clear names, cohesive modules, explicit dependencies, and useful
  documentation over clever code or speculative abstractions.
- Before editing **any file**, inspect the relevant implementation and present
  the impact assessment, credible risks or a justified no-risk conclusion,
  evidence gaps, validation plan, and confidence from **0% to 100%** for each
  proposal. Do not invent risk, certainty, test results, or performance gains.
- Consider coding-agent discoverability and testability in every change. For
  application features, also assess the actual native tool/controller path.
  Preserve human usability, explicit approvals, and safety boundaries.
- Fix the class, never the case. A fix must solve the general cause of a failure
  for any input, not the test, scene, prompt or benchmark that exposed it. Do
  not tune code, thresholds, defaults, word lists, special cases or model
  instructions to one case's names, wording, sizes or materials. Check a new
  instruction sentence or vocabulary across several unrelated cases first. Name
  the exposing case only in comments and documents, as evidence, and validate
  on held-out cases the fix was not developed on.
- Keep changes focused. Preserve unrelated edits, branch state, and upstream
  Blender behavior. Do not split or rewrite unrelated Blender files merely to
  satisfy an arbitrary size target.
- After implementation, report actual acceptance results, skipped checks,
  remaining risks, human/agent impact, and updated confidence per objective.

## Actual Code Layout

| Path | Responsibility |
| --- | --- |
| [source/blender/](source/blender/) | Native Blender integration; Console editor subtypes, RNA, window/editor lifecycle, and C++ sculpt kernels |
| [scripts/addons_core/angelo_agent/](scripts/addons_core/angelo_agent/) | Application-owned controller, provider adapters, tool schemas, dispatch, chat UI, staging, checkpoints, and task memory |
| [scripts/modules/_console_angelo.py](scripts/modules/_console_angelo.py) | Deferred native Coding Agent bootstrap |
| [scripts/modules/_console_angelo_genai.py](scripts/modules/_console_angelo_genai.py) | Native Generative AI editor bootstrap |
| [scripts/startup/angelo_default_layout.py](scripts/startup/angelo_default_layout.py) | Angelo startup layout |
| [scripts/angelo/](scripts/angelo/) | Native development build and scratch launch/recovery entry points |
| [tests/python/angelo_agent/](tests/python/angelo_agent/) | Dependency-free contract tests and actual Blender/GUI fixtures |
| [doc/angelo/](doc/angelo/) | Project-owned subsystem contracts and validation boundaries |
| [release/branding/angelo/](release/branding/angelo/) | Angelo branding assets |

Change native source directly in this checkout. The project does not use a
separate Python/C++ source-overlay tree. Angelo identifiers are real contracts:
the `angelo_agent` add-on module, `angelo_*` agent tool names and
`TOOLSET_VERSION`, `angelo.*` operator idnames and `ANGELO_*` class names,
`ANGELO_*` environment variables, the `angelo`/`angelo_genai` Console languages,
`Mesh.angelo_*`/`Object.angelo_*` RNA, `_angelo_*`/`angelo_*` saved custom
properties, the `angelo-agent` state folder and the external CLI protocol.
Saved preferences, keymaps, .blend files, chats and scripts depend on them; do
not rename one without a migration. Pre-rename names survive
only as compatibility fallbacks, each owned by one place: deprecated
environment spellings in `product_env.py`, folder moves in `data_migration.py`
and `state_migration.py`, saved custom properties and tool names in
`legacy_names.py`, and preference, keymap and Console-language renames in
`source/blender/blenkernel/intern/angelo_legacy_names.cc`. The complete list is
"Legacy names" in [the native build document](doc/angelo/native_editor_build.rst);
new code, tests and documentation never introduce them.

## Contracts to Read for the Affected Subsystem

- [Native editor and build](doc/angelo/native_editor_build.rst): Console-backed
  editor identities, independent input buffers, lazy bootstrap, save/load, the
  isolated macOS arm64 development build and the full-feature macOS arm64 build
  with its signing-readiness findings, and how .blend saves replace a link
  planted at their temporary name (a change from upstream Blender).
- [Application identity](doc/angelo/app_identity.rst): the one JSON source of
  product, vendor, bundle/package ids, GUIDs, folder names and links, the
  side-by-side rules with a stock Blender, the window chrome colour (title bar
  and top bar) and its text-contrast rules, the installer artwork (disk image
  window, WiX bitmaps) and the text it must carry, and the migration of older
  settings.
  Never hard-code one of those values elsewhere.
- [Agent integration](doc/angelo/agent_integration.rst) and
  [native execution](doc/angelo/agent_execution.rst): established provider
  sessions, direct typed dispatch, request ownership, staging, previews,
  visual observation (viewport views, bounded rendered looks and finished
  pictures, their budgets and what each leaves unchanged), acceptance,
  cancellation, and Undo. [Pictures beyond the viewport](doc/angelo/view_sources.rst):
  Image pixels, UV layouts, weights, colour attributes, render passes, the
  person's editors and motion strips, never loading a file or showing code,
  credentials or paths.
- [Native agent chat](doc/angelo/agent_chat.rst): the chat editor's composer,
  model, effort and Fast controls, approval modes and cards, attachments,
  collaborators, web and file tools, request snapshots and opt-in voice.
- [Checkpoint policy](doc/angelo/checkpoint_policy.rst) and
  [task memory](doc/angelo/task_memory.rst): explicit snapshot boundaries,
  deduplicated storage, automatic retention, document binding, and evidence
  invalidation.
- [Scene intelligence](doc/angelo/scene_intelligence.rst) and
  [local verification](doc/angelo/local_verification_and_recipes.rst): bounded
  observations, current handles/revisions, live data-path reads and .blend file
  summaries with what they never return, closed recipes, and result checks.
- [Native sculpting](doc/angelo/native_sculpting.rst): native kernel ownership
  and sculpt/Undo safety.
- [Native coverage checklist](doc/angelo/native_coverage.rst): what agents can
  do natively, partially, or only through reviewed Python, with coverage
  metrics; update it with every action kind you add or change.
- [External coding-agent CLI](doc/angelo/external_cli.rst): the
  off-by-default local `angelo` transport, read-only in phase 1, with its
  allowlist, envelope, exit codes, authentication, and validation fixtures.
- [Generation Flows](doc/angelo/generation_flows.rst): node-tree GenAI flows,
  input keys and reuse, one itemized approval per run, and the run cards.
- [Background jobs](doc/angelo/long_jobs.rst): renders, bakes and exports as
  Blender's own jobs with progress, Cancel, restored settings and safe outputs;
  long or unbounded work uses this contract, never a typed program.
- [Team library and IPs](doc/angelo/team_library.rst): the signed-in team's
  library and IP tools, their untrusted-file checks, approvals and prompt cost.
- [Generative Studio](doc/angelo/generative_studio.rst) and
  [AI providers](doc/angelo/ai_providers.rst): generation, authentication,
  billing, and approval boundaries; the team account, team connections and
  the Angelo gateway.
- [Managed policy](doc/angelo/enterprise_policy.rst): what an organization's
  IT department can lock (macOS managed preferences, Windows policy keys),
  where it is enforced, and team sign-in for administrators.
- [Releasing Angelo](doc/angelo/releasing.rst): the release procedure, in
  order: macOS Developer ID signing, notarization and the styled disk image;
  Windows Authenticode with a Cloud KMS key; publishing the installers and
  verifying them; the update-feed key and feed; the GPL source archive and offer; the bundled agent runtime (building, checking,
  signing and its notices); what the app bundle ships from `scripts/angelo`;
  and which steps need the release's own signing credentials.
- [Updates, crash reports and release notes](doc/angelo/updates_and_reports.rst):
  the notify-only update check and its signed feed (key ceremony, publishing),
  the crash dialog, the report and its server contract, What's New and
  `CHANGELOG.md`, and what the website serves.
- [Shared UI components](doc/angelo/ui_kit.rst): the `ui_kit*` controls (chips,
  notices, code blocks, toggles, fields, dropdowns, rows, tiles, scrollbar, tooltip) every
  Angelo-drawn surface uses, in cards and on the chat canvas.
- [Localization](doc/angelo/localization.rst): Angelo's interface in 21 languages,
  the second text domain and its catalogues (`locale/angelo`), how every string
  reaches `ui_text`/`IFACE_`, Arabic and right-to-left handling, the pseudo-locale and
  the rule that model-visible text is never translated.
- [Performance](doc/angelo/performance.rst): how agent work stays fast, the
  mechanisms and budgets of each hot path, the instruction ratchet and how to
  run it, and the measurements behind each design with their method and limits.
- [Fast-tier reliability](doc/angelo/fast_tier_reliability.rst): the patterns
  that cost cheaper models extra rounds or failures, the host's frame-change
  recovery, the harness's input isolation and the replay tools.
- [Agent stack](doc/angelo/agent_stack_integration.rst): toolsets 21 and 22
  (named objects in deltas, superseded plans, handle targets, reply-less
  edits), rate-limited (invalid) harness trials and how a gate run is graded.
- [GPT models through Codex](doc/angelo/gpt_launch.rst): the bundled agent
  runtime (a patched Codex) and its prompt-cache keys, Codex code mode,
  tool declarations and their budget, the prompt prefix, Codex typed plans
  and their strict schema, and the Codex side of the harness.
- [Scenario benchmarks](doc/angelo/scenario_benchmarks.rst): multi-turn
  builds (the bridge and the island) run on Angelo and Blender MCP against the
  same reference images, their closed checks and end-of-session scoring, the
  dry run that needs no provider, the trial-window recording, and how to run
  and add a scenario.
- [One-shot tests](doc/angelo/oneshot_tests.rst): one message with reference
  images per side and nothing after it; the contract, the fairness rules and
  what the MCP side's client is allowed, grading by closed checks and pixel
  checks, the path metrics, the time and cost caps, the dry run that needs no
  provider, and how to run a test.
- [Debug mode and asking why](doc/angelo/debug_mode.rst): the harness's
  opt-in diagnostic runs (`--debug-thinking` records Claude's thinking
  summaries, `--ask-why-on-miss` asks the agent about each failed check after
  grading), how they stay out of results and claims, and how to diagnose a
  miss.
- [Final measurement runs](doc/angelo/final_runs.rst): the driver for
  release-gate runs and for comparison runs against stock Blender with an MCP
  add-on on a frozen build: matched conditions, cold-start and returning-user
  columns, the quiet-machine guard, resume and the commands.
- [Prompt audit](doc/angelo/prompt_audit.rst): what each route's model
  actually received, read from recorded requests: the fact registry, foreign
  text per runtime version and per-route digests; a release check.
- [Model routing](doc/angelo/model_routing.rst): per-session prompt-cache
  TTL, per-model native contexts, Auto routing behind a measured gate,
  escalation, standing helper consent, capability profiles, how a measured
  model is admitted (implemented vs design is stated there).

Do not infer current behavior from an old test receipt or a planned feature.
Trace the current source and its tests before proposing a change. Keep subsystem
details in the existing project documents rather than duplicating them here.
