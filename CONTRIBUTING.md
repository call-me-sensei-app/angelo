<!-- SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd. -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Contributing to Angelo

Angelo is built directly on Blender. The actual source layout and subsystem
documents are listed in [AGENTS.md](AGENTS.md). These are this repository's own
engineering rules, for human contributors and coding agents alike.

## Engineering Standards

The goal is performant, organized, human-readable code that people can maintain
**without AI assistance**. These standards apply to every change by humans or
coding agents. They do not require an AI service to understand, build, or test
ordinary local code. Isolate and document checks requiring an application,
provider account, network access, or paid service.

### Human Maintainability

- Use descriptive names, explicit control flow, small cohesive functions, and
  clear ownership. Prefer established patterns over hidden side effects,
  speculative abstractions, excessive indirection, or catch-all utility modules.
- Separate controller/provider transport, domain logic, validation, persistence,
  and presentation. Reuse existing owners rather than duplicating business rules.
  Integrate native changes with Blender's existing RNA, editor, allocator,
  registration, Undo, and lifecycle conventions in the affected subsystem.
- Document interfaces, invariants, units, ownership, failure recovery, and
  non-obvious decisions. Explain why in comments rather than narrating the code.
  Keep setup, examples, and targeted test commands understandable to humans.
- Make errors and state transitions actionable. Do not swallow broken invariants
  or report submission, partial recovery, or an unverified read-back as success.
- Keep patches focused and reviewable. Update affected documentation and tests
  with behavior. Preserve unrelated edits and existing upstream behavior; do not
  perform large cosmetic refactors or impose borrowed file-size/layout rules.

### Performance

- Assess realistic scene sizes, algorithmic complexity, UI responsiveness,
  allocations, memory, dependency-graph work, startup, disk/network I/O, and
  resource lifetime in the affected paths. Avoid redundant traversal, copies,
  serialization, provider rounds, redraw work, and unbounded queues or caches.
- Profile suspected bottlenecks before optimizing. For hot-path changes, compare
  representative workloads and environments, or define and measure a budget for
  a new path. Report method, inputs, timings, and limitations; never invent gains.
- Keep provider I/O off the UI thread and Blender data access on its main thread.
  Yield cooperative work at safe boundaries; do not promise a hard frame-time
  bound for a long native kernel or move `bpy` calls onto workers to hide latency.
- Keep draw callbacks free of disk I/O, resource loading, and Blender datablock
  mutations. Prepare bounded data before drawing. Caches need ownership,
  invalidation, bounds, and cleanup across document changes and unregister.
- Preserve correctness, scene isolation, and Undo when optimizing staging or
  snapshots. Do not retain private staging data in the artist's Main merely to
  avoid serialization, or skip required verification to reduce elapsed time.
- Prefer the simplest implementation meeting measured needs. Explain necessary
  low-level optimizations and test their invariants. Assess dependency costs
  before adding them. If performance is unaffected, explain why; if unmeasured,
  report it as unverified rather than claiming it is optimized.

### Agent Readiness

Every change must consider repository coding agents. Application features must
also consider the native agent controller and tool path where applicable. New
features must be easy for coding agents to locate, understand, change, and test,
without compromising human usability or making AI a maintenance requirement.

- Keep module ownership, interfaces, subsystem documentation, examples, and
  targeted tests current. Use one shared policy, not divergent per-model rules.
- Extend the existing tool/controller and native execution owners. Do not add
  a parallel provider session engine, screenshot-driven execution layer, model
  router, or tool server merely to make a feature agent-accessible. The only
  exception is the off-by-default, local-only, authenticated `angelo` CLI
  transport ([external CLI](doc/angelo/external_cli.rst)) and the MCP stdio
  launcher that speaks through it to external AI apps
  ([external MCP](doc/angelo/external_mcp.rst)), including the headless child
  that launcher starts on a .blend over inherited pipes: they route into the
  existing controller and executor owners (the direct-tools call path,
  `request_tool`, ordinary chat turns) and add no tools, schemas, approvals, or
  authority.
- Keep capabilities, parameter schemas, bounds, preconditions, side effects,
  permissions, structured results, and actionable errors explicit. Assess toolset
  versioning and resumed-session compatibility before changing tool contracts.
- Use actual document-scoped handles, observed revisions, and declared mutable
  dependencies. Names, remembered approvals, and copied identifiers must not
  replace current ownership/evidence. Reject stale work rather than overwrite
  intervening human changes.
- Preserve approved/read-only boundaries, mutation-ID protection, staging
  isolation, explicit preview acceptance, the existing Undo-enabled commit path,
  and snapshot/recovery policy. Missing checks must not invent objective success.
- Expose actual request identity, progress, completion, failure, and verification.
  Define retry behavior; inspect receipts before retrying a timed-out mutation.
  Stop operates at safe boundaries, not by killing an atomic native kernel.
- Keep provider credentials, authentication, billing, and consent in their
  existing owners. Do not create new authority from a project note, brief,
  agent request, or display-only UI control. Never leak credentials or user data.
- Test human and agent paths, including failure, cancellation, recovery, and
  stale-state rejection where relevant. For changes with no application-agent
  impact, explain why; do not add an unnecessary tool just for compliance.

### Required Coding-Agent Workflow

#### Before Any Changes

Before editing **any file**, inspect the relevant project guidance, source,
callers, consumers, tests, and lifecycle, then present an assessment to the
developer. Analyze direct and indirect impacts thoroughly, with depth matched to
the actual scope. Documentation-only work does not need invented runtime risks
or irrelevant benchmarks.

1. **Objective and acceptance criteria:** State the requested outcome and how
   to verify it. Separate required work from optional ideas.
2. **Evidence and current behavior:** Reproduce a bug or cite concrete source/test
   evidence. Distinguish observations from hypotheses and historical receipts.
3. **Scope and impact:** Identify affected files, dependencies, behavior, and
   contracts. Assess relevant performance, threading, UI, save/load, Undo,
   staging, provider, security, build/platform, and agent implications. State
   scope-based reasons for N/A dimensions and identify uninspected systems.
4. **Approach and alternatives:** Explain the smallest coherent change and
   material tradeoffs. Do not present a speculative fix or unrelated redesign
   as necessary work.
5. **Risk assessment:** Report credible failure modes, supporting evidence or
   mechanisms, affected users/data/contracts, severity, likelihood when
   assessable, and mitigation. Keep unknowns separate. Do not invent risks or
   numerical probabilities to fill a template. **"No risk identified within the
   assessed scope" is valid**, with scope and evidence; it does not prove that
   unexamined areas are risk-free.
6. **Confidence:** Score each proposed change or fix from **0% to 100%** for
   meeting its stated objectives. For a bug, separately score the diagnosis when
   uncertain. Explain the evidence, assumptions, and validation gaps. These are
   reasoned judgments, not statistical probabilities. Never force a 100% score.
   Use **100% only for a bounded claim fully supported by available evidence**;
   do not imply all environments or future behavior are proven.
7. **Validation and reversal:** Plan relevant unit, native runtime, GUI, agent,
   and performance checks, and rollback/recovery where applicable. Disclose
   unavailable environments. Do not test against an unrelated production scene.

Publish the assessment **before implementation**, not retroactively. Update it
before a changed approach when new evidence changes scope, diagnosis, risk, or
confidence. Investigate uncertainty material to correctness/safety and ask the
developer when a consequential decision needs their input. Routine in-scope
work does not require extra approval or an artificial 100% confidence threshold.

#### Implementation and Delivery

- Implement the assessed scope. Preserve unrelated work and branch state; never
  weaken tests, approval gates, or safety contracts to make a patch appear done.
- Run relevant checks and inspect the final diff. Dependency-free source/mock
  tests do not prove actual Blender behavior, platform support, authentication,
  paid services, or natural-language reliability.
- For changed native/UI behavior, use the actual compiled development binary
  with isolated test state and a replayable fixture. Check state, relevant
  pixels, process/shutdown logs, and fixture receipts. A zero exit status alone
  does not prove a GUI fixture's `success` or complete phase.
- Report changes, acceptance results, actual checks, skipped checks and reasons,
  remaining risks/unknowns, human/agent impact, and **updated confidence per
  objective**. Suggested, implemented, and verified fixes are different states.
- Keep assessment reports in the conversation/review. Durable documents describe
  current contracts and rationale, not accumulated debugging transcripts.

Use this reporting outline, expanding it when the impact warrants it:

```text
Before editing
Objective and acceptance criteria:
Evidence and current behavior:
Scope and direct/indirect impact:
Approach, alternatives, and tradeoffs:
Risks and mitigations (or no risk identified, with scope/evidence):
Unknowns and uninspected areas:
Confidence per proposal: X% — evidence, assumptions, and validation gaps
Bug diagnosis confidence, if applicable: Y% — evidence
Agent impact: repository coding agents; application agents or justified N/A
Validation plan and reversal/recovery:

After implementation
Changes and acceptance results:
Checks run and outcomes; skipped checks and reasons:
Remaining risks/unknowns; human and agent impact:
Updated confidence per objective: X% — evidence and scope
```

## Build and Validation

The current source version is Blender 5.2.2 LTS. The validated development preset
is macOS arm64 editor validation, not a full release or proof of Windows/Linux
support. Use [native_editor_build.rst](doc/angelo/native_editor_build.rst) for
pinned prerequisites, binary paths, overrides, and compiled/GUI fixture commands.
`scripts/angelo/build_release_macos.sh` builds the full release feature set (Cycles,
OpenVDB, USD, Alembic, FFmpeg and the rest) from the same document's "Full-feature
macOS arm64 build"; it is a separate build, so run the native suites on both.
Do not download prerequisites, change credentials, replace an installed Blender,
or invoke an unrelated checkout's build scripts without an assessed reason.

From this repository root, after the documented prerequisites are present:

```sh
scripts/angelo/build_native.sh

# Dependency-free rules validation; use another test_native_* pattern for
# the affected subsystem's targeted standalone tests.
python3 -m unittest discover -s tests/python/angelo_agent -p 'test_native_repo_rules.py'

# Whole host suite, listing every skip. CI installs NumPy and jsonschema and
# adds --max-skips 0 (.github/workflows/host-tests.yml).
python3 tests/python/angelo_agent/run_host_tests.py

# Actual compiled native editor contract, with the default development build.
ctest --test-dir ../build-angelo-native -R '^angelo_native_editor_core$' --output-on-failure

# Native Blender suites against the compiled binary, each in fresh isolated
# state: BACKGROUND by default, --gui adds window suites, --list shows all.
python3 tests/python/angelo_agent/run_native_suites.py \
  --binary "$PWD/../build-angelo-native/bin/Angelo.app/Contents/MacOS/Angelo" --jobs 2
```

[native_suite_manifest.py](tests/python/angelo_agent/native_suite_manifest.py)
classifies every `test_blender_*.py` suite as BACKGROUND, GUI or LIVE; the host
suite fails when one is unclassified. LIVE suites use real providers and may
bill the provider accounts you connect, or (`test_blender_keychain_live`) write and delete one
throwaway item in your login Keychain: the runner starts one only with
`--allow-live` and an exact `--include` of its name, and the suite still
requires its own opt-in variables. The runner sets `ANGELO_DISABLE_KEYCHAIN=1`
for every suite, so the app never reads, writes or waits on an OS key store
(Keychain, Credential Manager or keyring) there. A suite passes only with a clean exit, a fresh successful receipt or
completion marker, and no native assertion, allocator leak, crash or Python
traceback in its logs; see
[agent_execution.rst](doc/angelo/agent_execution.rst).

Only host tests run in CI. Native suites run locally with this runner
before merging: they need the compiled app and pinned libraries, which already
exist on a development machine, while a hosted runner would have to fetch and
build them for every run.

### Before merging

| Check | Where it runs | What it proves |
| --- | --- | --- |
| `run_host_tests.py --max-skips 0` | CI (Linux) and locally | Contracts, schemas, manifest, and that `perf_ceilings.json` was measured on the current add-on and benchmark sources |
| `run_native_suites.py` (BACKGROUND; `--gui` where windows changed) | macOS development machine | Behaviour in the compiled app |
| `run_native_suites.py --include test_blender_perf_ratchet` | macOS arm64 only (instruction counters) | No benchmark operation exceeds its instruction ceiling, every ceiling is measured |
| Live-agent evaluations (`eval_live_agent.py`, opt-in) | macOS, for agent changes | Live-agent reliability and latency; they send model requests billed to the account you connect |

A change to `scripts/addons_core/angelo_agent/` or to the benchmark harness
fails the host suite in CI until the instruction ratchet has run on that code:
run the perf-ratchet suite, then
`python3 tests/python/angelo_agent/perf_ratchet.py <run>/test_blender_perf_ratchet/qa/fixture/perf-ratchet.json --stamp`,
and commit `perf_ceilings.json`. A ceiling only rises with
`perf_ratchet.py RESULTS --update --allow-raise` and the reason in the review
(see [performance.rst](doc/angelo/performance.rst)).

**No builder paths, no credentials.** Nothing that ships may embed the path,
user name or host name of the machine that built it, or any credential.
`release_checks.py builder-paths` scans every file of a release bundle and
installer payload, binaries included, and every release stage fails on a hit;
the build maps its own paths away (`build_files/cmake/angelo_build_paths.cmake`).
Never weaken the check. An exception goes in
`scripts/angelo/builder_paths_allow.json` only for an unchanged third-party file,
with its exact match and a reason a reviewer can verify (see "No builder paths"
in [releasing.rst](doc/angelo/releasing.rst)).

Build output defaults to `../build-angelo-native`, outside the source checkout.
`ANGELO_NATIVE_BUILD_DIR` and `ANGELO_BUILD_JOBS` are the overrides; the
pre-rename `SENSEI_NATIVE_BUILD_DIR`/`TOONLAB_NATIVE_BUILD_DIR` and
`SENSEI_BUILD_JOBS`/`TOONLAB_BUILD_JOBS` spellings remain deprecated fallbacks.
Every product variable follows that rule (see "Legacy names" in
[native_editor_build.rst](doc/angelo/native_editor_build.rst)).
When changing this preset or documentation, verify against the actual script
and source version.
Update the relevant existing documents under [doc/angelo/](doc/angelo/).

Contributions are accepted under the license of the files they change and the
[Developer Certificate of Origin 1.1](DCO). Sign off every commit with `git commit -s`, which
adds a `Signed-off-by: Your Name <you@example.com>` line certifying the DCO; commits without a
sign-off are not accepted.

New project files must carry SPDX metadata consistent with the affected
subsystem; the project's own notice is `Hyperbond Studio Pte. Ltd.`, and it applies
only to the project's own files. Preserve existing Blender and third-party
attribution; do not invent copyright owners, remove notices, or change
licenses as part of a branding cleanup.
