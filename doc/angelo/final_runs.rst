.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Final Measurement Runs
**********************

``tests/python/angelo_agent/final_runs.py`` drives a measurement campaign on a
frozen build of Angelo: a release gate (Angelo only), then a head-to-head
against stock Blender with a Blender MCP add-on and server. It measures nothing
new: every step is a command of an existing harness (``bench_mcp_vs_angelo.py``,
``eval_live_agent.py`` and, optionally, an external Object Mode pilot runner),
run one trial at a time on a quiet machine, and the campaign can stop and
resume anywhere.

.. contents::
   :local:
   :depth: 1

What runs
=========

The steps, their order and their planning estimates are in
``final_runs_plan.py``; ``final_runs.py plan`` prints every step with its
command and estimate. The models are those of ``final_runs_plan.MODELS``.

**Gate** (phase ``gate``, Angelo only), per model: the default task set of
``bench_mcp_vs_angelo.py`` (five trials) and its long set (40 tasks, three
trials), both with every task graded (``--after-failure reset``), then a live
smoke through ``eval_live_agent.py`` (seven gate tasks and eight survey
questions), and an Ask smoke: the default set, one trial,
``--angelo-approval ask``. The gate runs in Auto, the product default; the Ask
smoke keeps the other mode, where every typed edit waits for the tester,
exercised. Every gate step runs parallel edit as shipped
(``--angelo-parallel-as-shipped``, ``--parallel-as-shipped`` for the live
smokes): the switch is left unset, so each trial's Angelo decides by its own
capability check, as for a person. The step builder adds it and refuses a gate
step that sets the switch itself. Each trial's report records Angelo's own launch
line (``parallel_edit``, from its ``run.log``: "Angelo: parallel edit on: ...",
with "(forced at launch)" only when a switch was set). The bar is every task
of every step.

**Head-to-head** (phase ``h2h``, both sides, five trials a step; the
returning-user step is three launches): the default and long sets and the
five mini one-shot tests; on Codex runtimes also the
scene one-shots footbridge, bridge, tree and cottage (`oneshot_tests.rst
<oneshot_tests.rst>`_; the footbridge and the bridge with their unambiguous
briefs, never their ``-conflict`` variants, which are regression checks of
Angelo alone) and the returning-user step (`Cold start and returning
user`_); and, on macOS with ``init --pilot-dir``, the Object Mode pilot cases
(batch edits, duplication, material assignment, node editing), which run
through the pilot's own runner and interpreter and are left out of a campaign
without one.

**The heavy cases**, on every model and both sides, five trials each: the
castle one-shot (``h2h-<model>-oneshot-castle``, one message) and the five
edits of the dense castle scene (``h2h-<model>-scenario-heavy``, five
messages in one chat; `scenario_benchmarks.rst <scenario_benchmarks.rst>`_,
"The heavy scene"). They run with the other long steps, at the end of each
connection's block. Both need their reference folders on the machine first
(``scenario_references.py --scenario oneshot_castle`` and ``--scenario
heavy``, which also makes the heavy scene's 250 MB start file). To run only
them, or one of them::

    $P tests/python/angelo_agent/final_runs.py run --output <campaign> --authorize-billed-runs \
        --only 'h2h-*-oneshot-castle' --only 'h2h-*-scenario-heavy'

The two sides are matched:

* Sides alternate inside every trial, so neither always goes first. Two trials
  never run at once on one machine: they would contend each other's timings.
* Every bench step runs with the same model, effort and prompts on both sides
  and ``final_runs_plan.COMMON`` (effort low, one stable working folder, the
  chat editor's turn context, real input ignored in the trial windows).
* Both sides get the same window state (``final_runs_plan.window_state``):
  behind other applications where stock Blender honours ``--no-window-focus``
  as Angelo does, and on macOS, where stock Blender activates and opens in front
  whatever the flag, in front for both (``--window-state front``), so a macOS
  campaign takes the screen and runs on a Mac nobody is using. A step with
  Angelo alone stays behind. A campaign whose plan does not record the window
  state cannot be resumed with ``--resume``: its plan differs.
* Neither side's runtime reads project context (``bench_workdirs.py``). A
  client loads the instruction files of its working folder and every parent
  (Claude Code ``CLAUDE.md`` and ``.claude/``, Codex ``AGENTS.md``) and, in a
  git repository, the repository's status. So each runtime works in an empty
  folder from which no ``CLAUDE.md``, ``AGENTS.md``, ``.claude/`` or ``.git``
  is visible: the run folder's own when nothing is visible from it, else one
  under the temp folder (``ANGELO_BENCH_WORKDIR_ROOT`` overrides it), and a
  trial is refused before anything starts when its folder, or the client's
  own home (``$CLAUDE_CONFIG_DIR/CLAUDE.md``, ``$CODEX_HOME/AGENTS.md``), would
  give it context. Angelo's runtimes work in the trial's state folder, moved
  into the trial when done; its Claude Code also runs with
  ``--setting-sources ""``, so no ``CLAUDE.md`` reaches it, though a git
  status still would.

  A run folder inside a checkout would put the checkout's instruction files
  and git status into each side's first message, which enlarges that message
  (by tens of kilobytes for a large ``CLAUDE.md``) and its cost. That is why
  the guard refuses such a folder rather than warning about it.
* Angelo's chats keep the product's default approval, Auto bounded edits: its
  typed bounded edits run without asking, as for a person. ``--angelo-approval
  ask`` (``--approval ask`` for the live eval) measures Ask instead, and
  ``--angelo-always-allow-scripts`` (``--always-allow-scripts``) the person's
  opted-in Always allow scripts; each trial reports the mode its chat had.
* In the head-to-head and the one-shot steps, Angelo runs with the product's
  own Always allow scripts setting (``--angelo-always-allow-scripts``), never
  the chat-level Trusted scripts test switch, because the MCP side runs
  model-written scripts without a prompt. Every report from those steps states
  "scripts allowed" (``bench_integrity.scripts_label``); the gates stay in Auto
  with no script opt-in.
* ``init`` records each step's planned trials per side (``planned_n`` in
  ``CONFIG.json``) before any trial. ``report`` gives each row its planned and
  valid n and lists under ``n_differs`` every arm whose valid n differs (a
  step not run, cut short, run with ``--max-trials`` or rerun), so no number
  is read as the planned comparison when it is not.
* The head-to-head starts only when every gate step is at its bar
  (``run --continue-after-gate-failure`` overrides): a gate below its bar
  changes the candidate and would void every later run.

Cold start and returning user
=============================

The report has two columns, measured on both sides. *Cold start* is a fresh
profile per trial (the bench empties Angelo's state before every trial): no
remembered Codex base, so on the person's own Codex Angelo's first request is
rarely cached, and the background warm-up its first completed turn starts is
counted from the request log. On Angelo's agent runtime there is no base: its
cache-affinity key is per account and prefix, under the per-user secret in
Angelo's data folder, which a trial's own state folder does not hold, so every
trial after a key's first use reads the cached opening (``performance.rst``,
"Cache-affinity keys"). A campaign that must give the same keys on another
machine or user sets ``ANGELO_CACHE_KEY_SECRET`` (64 hexadecimal digits) in
the bench's environment, which ``eval_live_agent.py`` passes to every trial;
keep it out of the results and the journal like any credential.

*Returning user* is the ``returning-<model>-default`` step (Codex runtimes
only). The bench's ``--returning-user`` keeps one Angelo profile over three
launches (``eval_live_agent.py --profile-dir``: Angelo's state with the
remembered Codex bases, the Blender user folders and the connection copy) and
opens a new chat per task; the MCP side opens a new Codex thread per task on
each launch. ``--request-log`` writes every Codex thread call and request to
``qa/requests.jsonl`` (thread IDs, fork source, warm-up flag, each request's
input, cached and output tokens; never a prompt or a credential), and each MCP
task records its thread and first request. Launch 1's first task is the cold
start, the first task of launches 2 and 3 is a relaunch, and the other tasks
are new chats in a running app. ``final_runs_report.returning_columns`` gives,
per side and position, the tasks, how Angelo's chats began (a fork of a warm
base or a fresh start) and the median cached share of each chat's first
request.

Cold start by launch position
=============================

The two numbers are classified by launch position, never by a cache hit (``bench_cold_warm.py``). A trial's first task is a **cold
start** when it is the first chat after a fresh launch, on either side: every
trial of an ordinary step (each is a fresh process pair, scene and session)
and launch 1 of a returning-user step. Launches 2 and 3 of a returning-user
step are **returning-user** starts, reported apart. The provider's prompt
cache can outlive a trial, so a cold start may read a cached opening; the
first request's cache read and write are shown beside it for information.
They come from records each side already keeps: on the MCP side the raw
stream (``claude-stream.jsonl``; on Codex ``codex-stream.jsonl`` or the task's
``first_request``), on Angelo's the Claude Code transcript or, on Codex, the
first chat in the ``--request-log`` log; otherwise the first task's own usage
when it made one request. A value no record gives is unknown, never guessed.

A start's number is its first task's wall time and list price plus every
warm-up of that launch, its wall time and its price, each counted once, and
any time the first message waited for a readied opening (the labelled extra
``--angelo-opening-wait``). The runtime's launch and checks (Claude Code or
the Codex app-server) are inside the first task on both sides; the app's own
launch (Angelo or stock Blender) is outside it on both sides. A first task
whose runtime was already running is marked *prestarted*. A warm-up's wall
time is added in full even when it ran beside the first task, so the number
is then an upper bound.

Angelo's bundled Codex, the cache-keyed agent runtime, sends no warm-up at
all: ``codex_bases.warm`` and ``codex_connect.prepare_opening``
refuse on a process with cache-affinity keys, and a new chat reads the cached
opening through its key (``test_native_cache_affinity.py`` pins this). Only a
stock Codex (``ANGELO_AGENT_RUNTIME=off`` or a development build without the
runtime) still warms up; any warm-up that runs leaves a receipt and is counted.

``SUMMARY.md`` lists every trial's launch, start, first-request cache use,
warm-ups, start number and totals over the head-to-head tasks, then a *cold
start vs cold start* line with both sides' medians and, for a returning-user
step, a *returning user* line. ``bench-mcp-vs-angelo.json`` keeps the same
under ``summary.cold_warm`` and each trial's launch and first request on its
first record (``trial_first_request``); ``report`` gives each step's
``cold_warm``, read from the step's folder for a live eval and for a bench
step recorded before it.

What is recorded
================

Per request: wall time; input, cached, uncached and output tokens; and the
API-equivalent cost at the model's list prices (``model_profiles.py``,
``bench_mcp_vs_angelo.price_records``). ``report`` gives per step and side
the passes, the failures, the median and 75th percentile seconds, the tokens
and the list-price cost, the scripts and the planned and valid n, the
contended trials, the returning-user columns and each trial's start (`Cold
start by launch position`_).

A value a request did not record stays unknown, never zero
(``bench_totals.py``). A step's ``list_price_usd`` and ``uncached_input`` are
``null`` when a request that ran recorded none, with ``*_recorded`` ("n of m
requests") beside them and ``list_price_known_usd``, the list price of the
priced ones; a skipped task sent nothing and adds nothing. The bench's
``SUMMARY.md`` does the same per trial: a trial's API calls or cost is
unknown when one of its tasks ran without it, and a median states how many
trials it covers.

Every comparison is priced on one basis for all its sides, stated once
(``cost_basis``; ``bench_totals.cost_basis``): Claude Code's own estimate
(``provider_usage.cost_usd``, the basis for Claude) when it was recorded for
everything every side ran, otherwise the list price (``list_price_usd``) on
every side. A Codex side records no estimate, so its comparisons are at list
prices on both sides; one Claude task without an estimate moves its
comparison to list prices too. One side's estimate is never compared with
another's list price. A comparison is the bench's ``SUMMARY.md`` (and its
``summary["cost_basis"]``), a step of ``report`` (``cost_usd`` per row,
``cost_basis[step]``), a one-shot test's report or group and a scenario's
scorecard (``cost_compared_usd``).

A price step is per request: a model with one (Haiku 5.5 above 100k prompt
tokens, ``price_step_tokens``) charges its higher rates only for a request
whose own prompt exceeds it. Usage summed over a turn's requests is priced
at the base rates when the sum is within the step, per request when each
request's usage was recorded (Claude Code's stream, Codex's per-update
``last``), at its own tier when the usage says it is one request, and
otherwise not at all; ``list_price_basis`` says which, or why the price is
unknown. Opus 5.5, GPT-6 Astra and GPT-6.1 Sol have no step.

Each cache write is priced at the TTL the provider reported for it, so both
sides are priced on the same basis; on Claude both agree with Claude Code's
own estimate (``provider_usage.cost_usd``) within rounding:

* Claude's usage splits every response's writes into 5-minute and 1-hour
  ones (``usage.cache_creation``). On a subscription Claude Code on the MCP
  side writes 1-hour entries, as Angelo's chats do; on an API-key or team
  route both write 5-minute ones (``model_routing.rst``, "Prompt-cache TTL
  per session"). The MCP side's ``provider_usage`` carries
  the turn's split (``cache_write_5m_input_tokens``,
  ``cache_write_1h_input_tokens``), summed over its distinct responses in the
  raw stream (``oneshot_metrics.claude_write_split``).
* Writes without a reported TTL are priced at an assumed one: Angelo's
  session TTL, the one each task's receipt recorded (``cache_ttl`` in the
  bench record; for records without it, 1 hour unless ``--angelo-cache-ttl``
  set another; Angelo's receipts carry no split) and 5 minutes for Claude
  Code (a response without a split).
* Codex usage has no split. GPT models have one write rate, which prices
  every write; a Codex-served model with two rates is priced at its
  5-minute one.

Each record's ``list_price_basis`` says which applied, and how many write
tokens were assumed. Pricing every write at the 5-minute rate would
under-price a side whose writes are 1-hour ones. A step recorded without the
split is priced again from its raw streams, sending nothing, by
``bench_mcp_vs_angelo.py --summarize-only <step folder>`` with the step's
``--task-set``, ``--trials`` and ``--model``; ``report`` reads the records as
last priced.

Every failure is kept: a trial a limit cut short is set aside under
``interrupted/``, a contended one under ``contended/``, never deleted.
``JOURNAL.jsonl`` records every trial, pause, refusal and wait with its
reasons, and ``STATE.json`` each step's progress.

Cache-read floors
=================

A cache read costs 0.05x input on the launch models, so one silent prefix
break (a timestamp in the instructions, a tool list in another order, a cache
key that changes per process) multiplies a chat's input cost by up to 20 while
every task still passes. ``cache_floor.py`` therefore checks every finished
gate step's prompt-cache reads against floors recorded in
``cache_floors.json``, and a read below its floor is a **cache SEV**: the gate
is not clear (``final_runs.gates_clear``), ``run`` ends with exit code 1 and a
``cache_sev`` journal event, and ``final_runs.py cache --output CAMPAIGN``
prints the check (exit 1 on a SEV). It reads only the receipts Angelo already
writes (each task's ``receipt.usage`` and ``receipt.route``):

* a **cell** is route mode | model | variant, the variant being the task set
  with the approval mode when it is not the default and
  ``new-chat-per-task`` for returning-user runs;
* **calls 2+**: per step and cell, the token-weighted cached share of every
  turn after a chat's first, leaving out turns after a gap of 5 minutes or
  more (``cache_gap``: the shortest provider cache lifetime), a first turn
  of several requests (its later requests cannot be separated), and the one
  turn with the lowest share. Providers occasionally serve one request
  without its cache, even under a minute after the previous turn (9 of 6,032
  later turns in the recorded runs read nothing), and leaving out the lowest
  turn absorbs one such request while a prefix or key break lowers every turn;
* **first call**, on routes with a cache-affinity key only
  (``route.cache_affinity``: Codex keyed and team routes): per step and cell,
  the median first-call share of the chats after the cell's first, which may
  be the one that warms a key the provider had expired. Claude has no key,
  so its first call is shown, never gated.

Unknown values are never a SEV, and a cell without a floor is listed as
unfloored, never failed. Each step cell also reports its full misses and its
lowest chat, for forensics.

**Floors.** For every cell, the lowest step value across the passing gate
campaigns ``regenerate`` was given, minus 5 percentage points, rounded down to
a whole percent: one rule for every cell and both metrics. A campaign with a
failed, inconclusive, skipped or unrun gate task is refused; ungraded survey
questions (no check exists) are not failures. The file names its source
campaigns and commits only, never a path::

   python3 tests/python/angelo_agent/cache_floor.py regenerate CAMPAIGN...
   python3 tests/python/angelo_agent/cache_floor.py check STEP_OR_RECEIPT...
   python3 tests/python/angelo_agent/cache_floor.py show STEP_OR_RECEIPT...   # per-chat shares

The checked-in floors come from the four latest passing macOS release gates
(the file names each campaign and its commit): calls 2+ floors of 91-93%
(lowest step values 96.1-98.6%) and first-call floors of 88-89% on the keyed
Codex routes (lowest 93.7-94.1%). Regenerate them after a gate-set or toolset
change that moves the reads, from passing gates only, and say why in the
review. Against 115 held-out step cells of older gate, head-to-head, smoke and
A/B runs the check raised no SEV; a synthetic broken prefix (only the first
3,072 tokens matching) and a key that changes per process both fail
(``test_native_cache_floor.py``). Routes without gate receipts (the API-key
and team routes) have unfloored cells.

The candidate
=============

``freeze`` makes one frozen copy of the built app (a clone on APFS), never
overwritten and checked against the source file by file. ``RECEIPT.json``
records its executable's digest, a digest of the whole inventory and
``--version``. Every step checks the executable again and a real run checks
the whole inventory, so a rebuild during the campaign cannot mix conditions.

``init`` refuses pilot configs whose Angelo is not the frozen candidate, whose
stock Blender differs from ``--stock-blender``, or that point at a rehearsal
freeze. ``init --rehearsal`` marks a rehearsal campaign, which ``run``
refuses. ``run`` also refuses a checkout with uncommitted changes, because the
harness code a trial loads must be the campaign's commit.

The quiet machine
=================

Before every trial the 1- and 5-minute load averages must be at or below the
threshold (``--load-threshold``, 6 by default; on Windows the busy cores) and
no other heavy process may run: a build or test tool by name or another
harness; and programs outside the run may not use more cores together than the
threshold (a single resident desktop program near one core is no contention by
itself) (``final_runs_load.py``). Otherwise the run
pauses, writes ``paused`` with the reasons to ``JOURNAL.jsonl``, and resumes
when the machine is quiet (``resumed``); after six hours busy it stops (resume
later). While a trial runs, the load, the other heavy processes and the cores
used outside the run are sampled every 5 s into ``<step>-load.jsonl``, and the
trial's record says whether it was contended: a heavy process ran, or programs
outside the run used more cores than the threshold. The load average alone
does not decide it, as it includes the trial's own work (a one-shot's final
render uses every core). A contended trial is set aside and run again, once
by default (``init --contended-reruns``); a second contended run is kept and
marked, and ``report`` lists it.

What the guard cannot read is unknown, never quiet: a process list that
failed (``ps`` or PowerShell raised, timed out, exited with an error or
printed nothing) or a Windows load without a reading. Before a trial that
refuses the start with the reason ("the process list could not be read"),
and the run keeps waiting and retrying as for a busy machine, journaling the
reason, until it can read the machine or the six-hour limit stops it. A
sample taken while the list could not be read counts toward
``unreadable_samples`` and makes its trial contended, so it is run again.

An indicative snapshot on a busy machine (never the measurement itself) passes
``run --ignore-load``: no trial waits, every trial's journal entry records the
load and the heavy processes at its start and says ``load_guard: ignored``,
and a start a harness refuses is tried again after two minutes. Initialize
such a campaign with ``--contended-reruns 0`` (every trial would count as
contended) and cap it with ``--max-trials N`` where a step needs fewer trials.

Resume, stop and provider limits
================================

Run ``run`` again after any interruption: finished trials are skipped, a trial
cut short is set aside and run again (the harnesses' ``--resume`` and
``--max-new-trials 1``). ``touch <campaign>/STOP`` ends the run after the
current trial; ``--stop-after-hours H`` starts no trial after ``H`` hours.
``--retry-failed`` tries again the steps a start problem stopped.

A campaign uses two connections, one Claude and one ChatGPT. A provider rate
or usage limit (harness exit code 3) blocks that connection's steps for an
hour while the other connection's steps continue. Inside each phase, steps
alternate between the two connections and short steps come before long ones,
so one connection's limit recovers while the other runs and an early stop
still leaves results. Before each step on the ChatGPT connection (the Codex
runtime) the driver reads its rate-limit window without a model request
(``account/rateLimits/read``, also printed by ``headroom``) and, above 95 %,
waits for its reset so no trial is cut short. Claude Code has no such read.

Commands
========

From a clean checkout of the candidate's commit, a pinned, detached run
worktree nobody merges into (``oneshot_tests.rst``, "Pinned run worktrees":
the Angelo side imports this checkout's add-on, and the bench refuses one that
is not the app's), with the Python the harnesses use (``P``)::

    P=<the harnesses' Python>
    $P tests/python/angelo_agent/final_runs.py freeze --source <built Angelo app> --into <candidate folder>
    $P tests/python/angelo_agent/final_runs.py init --output <campaign> --candidate <candidate folder> \
        --connections-dir <connection store> --codex-home <signed-in Codex profile> \
        --claude-connection <id> --chatgpt-connection <id> \
        --stock-blender <stock Blender> --mcp-server-dir <Blender MCP server> \
        --mcp-addon-dir <Blender MCP add-on> [--pilot-dir <pilot runner> --pilot-python <its Python>]
    $P tests/python/angelo_agent/final_runs.py plan --output <campaign>       # order, commands, estimates
    $P tests/python/angelo_agent/final_runs.py dry-run --output <campaign>    # no provider
    $P tests/python/angelo_agent/final_runs.py headroom --output <campaign>   # rate-limit windows, no model request
    $P tests/python/angelo_agent/final_runs.py run --output <campaign> --authorize-billed-runs \
        [--phase gate|h2h] [--only GLOB] [--stop-after-hours H]
    $P tests/python/angelo_agent/final_runs.py status --output <campaign>
    $P tests/python/angelo_agent/final_runs.py report --output <campaign>

``init --discover-connections`` uses the one Claude and the one ChatGPT
subscription connection in ``--connections-dir`` instead of naming them
(``--claude-connection`` and ``--chatgpt-connection`` take their IDs, as do
``ANGELO_FINAL_RUNS_CLAUDE_CONNECTION`` and
``ANGELO_FINAL_RUNS_CHATGPT_CONNECTION``; nothing defaults them).
``prepare-connections --connections-dir <connection store>`` creates the two,
unsigned, each with its own CLI profile, and ``check-sign-in --connections-dir
<connection store> [--account claude|chatgpt]`` checks that each one's CLI
profile is signed in (existence of its sign-in files only; nothing is read or
sent). ``run`` sends model messages on those connections, so it refuses
without ``--authorize-billed-runs``; every other action sends nothing.

``dry-run`` checks every step without a provider, deeper with ``--depth``:
``plan`` runs each step's plan-only form; ``preflight`` also each harness's
pre-start checks (executables, sign-ins, connection, folder); ``launch`` also
each runtime's MCP side with no model; ``full`` (the default) also every
one-shot set's scripted-model run on both sides (`oneshot_tests.rst
<oneshot_tests.rst>`_, "Dry run") and the pilot's no-provider smoke. A
scripted run drives both MCP clients, Claude Code and Codex, whatever its
step's runtime, so it names both executables from the campaign's
``CONFIG.json``; a step's own command names only its client.

On Google Cloud Windows machines
================================

A campaign can run entirely on Google Compute Engine Windows machines, so no
desktop computer is held for it. ``scripts/angelo/gcp_windows_validate.sh``
drives each machine from a workstation with a signed-in ``gcloud``. It takes
``ANGELO_GCP_PROJECT`` (your Google Cloud project), ``ANGELO_GCP_ZONE`` (a
zone with T4 GPUs) and ``ANGELO_GCP_NAME`` (the machine's name, ``<machine>``
below; its network, firewall rule, service account and default bucket,
``<project>-<machine>``, are named after it). ``ANGELO_GCP_MACHINE`` sets the
machine type (``n1-standard-16`` by default) and ``ANGELO_GCP_GPU`` whether it
has a T4 virtual workstation GPU (1 by default). The machine sits in its own
network with no inbound rule except IAP's RDP range, and powers itself off
after each run. ``ANGELO_GCP_DRY_RUN=1`` prints what a ``bench`` or ``clone``
command would send and start, and changes nothing.

Several machines can share one campaign plan, each running a subset of its
steps (`Several machines`_).

**The GPU preference.** On the GPU test machines every side of every trial
renders Cycles on the GPU, set the same way (``tests/python/angelo_agent/
bench_gpu.py``; the maintainer's rule, 2026-10-21). Before the trial starts,
the trial's own Blender, Angelo's in ``eval_live_agent_session.py`` and stock
Blender's in ``bench_mcp_blender_startup.py``, lists Cycles' devices. It selects
OptiX (CUDA when OptiX lists none) with the GPU enabled and the CPU not, saves
the trial's own preferences, and sets every scene, including files opened
later, to device GPU. The receipt's ``gpu_preference`` holds the resulting
device line, such as ``Cycles OPTIX: Tesla T4; scene device GPU``. A trial
whose Blender lists no GPU fails at setup; nothing falls back to the CPU
silently. Angelo's own device setup leaves a backend someone set alone, so
both sides keep this choice.

``ANGELO_BENCH_GPU`` (``require`` or ``off``) is on by default on Windows and
off elsewhere. The process that starts a run decides it once and gives every
child the same value, so a Windows machine without a GPU must set it to
``off``. The unbilled runs check the device line: ``--dry-run-mcp`` reads it
from the stock Blender's ready file. ``--preflight-only``, which is the A/B
driver's dry run, and ``--dry-run-scenario`` start each side's own Blender in
the background for it. The line must name ``ANGELO_BENCH_GPU_EXPECT``, or else
the GPU ``nvidia-smi`` lists (``Tesla T4``).

What a machine holds
--------------------

``scripts/angelo/windows_final_runs.ps1`` (``Prepare-Bench``, with
``windows_bench_tools.ps1``) installs these once, checks them every time, and
records every version and SHA-256 in ``C:\angelo\bench\state\facts.json``
(copied to the machine's bucket after each session):

* Claude Code from npm (``@anthropic-ai/claude-code``) at the comparator's
  version (``ANGELO_BENCH_CLAUDE_VERSION``, by default ``claude --version`` on
  the workstation): the package's native ``claude.exe``.
* Stock OpenAI Codex, unpatched, the MCP side's comparator:
  ``codex-package-x86_64-pc-windows-msvc.tar.gz`` from the GitHub release of
  the agent runtime's pinned version (``ANGELO_BENCH_CODEX_VERSION``), checked
  against the SHA-256 GitHub publishes for it and OpenAI's signature.
* Stock Blender 5.2.2 from ``download.blender.org``, the current stock release
  and the Angelo build's own base (a portable archive; nothing is installed;
  ``ANGELO_BENCH_STOCK_URL`` names another). ``facts.json`` records the
  archive's SHA-256 (``zip_sha256``); for ``blender-5.2.2-windows-x64.zip``
  Blender publishes
  ``3849d17a682cba006075aaa3f3597ecb5c9c30ec31035b2e092c53e40679b535``.
* The workstation's MCP add-on and server, byte-identical, with their
  dependency versions on CPython 3.14.0 through ``uv`` (``final_runs_mcp.py``;
  ``ANGELO_BENCH_MCP_RUNTIME`` names the frozen runtime, and the bundle
  travels as ``gs://<bucket>/bench/mcp-runtime.tar.gz``). The runtime brings
  its own interpreter (uv's build, in the runtime's ``python`` folder) and
  copies every package file: the stage installs it as SYSTEM, and the bench
  runs as its own user, who cannot read an interpreter in SYSTEM's profile
  (uv's default folder, where the server would exit before its first line).
  An install without its own interpreter is made again. The bundle is
  byte-identical for the same runtime, so every send of an unchanged runtime
  finds the one install (``mcp-<digest>``) a campaign's ``CONFIG.json``
  names. The dry run's server probe keeps the server's stderr
  (``mcp-server-stderr.log`` beside it) and names it when the server ends
  before answering.
* The one-shot tests' source picture (the tree and the cottage cut their
  references from it; ``SOURCE_IMAGES`` pins its name and SHA-256). It is
  never in the repository and never public: it is uploaded once to the
  machine's private bucket with::

     scripts/angelo/gcp_windows_validate.sh oneshot-sources <folder holding it>

  which refuses a bucket without uniform bucket-level access, without
  enforced public access prevention or with an ``allUsers`` or
  ``allAuthenticatedUsers`` binding, checks the folder against the tests'
  pins (``final_runs_sources.py``) and uploads only the pinned files to
  ``gs://<bucket>/bench/oneshot-sources/``. ``clone`` copies them into the
  clone's own (equally private) bucket. Each stage downloads them to
  ``C:\angelo\bench\oneshot-sources`` (kept when it already holds the
  pinned files), ``Prepare-Bench`` checks them again, and the bench session
  runs with ``ANGELO_ONESHOT_SOURCES`` pointing there. Never make a public or
  signed URL for it.
* Node.js LTS, Python 3.13 and uv.
* The candidate: the build's MSI (``ANGELO_BENCH_CANDIDATE_MSI``), installed,
  its install folder frozen (``final_runs.py freeze``) and uninstalled again;
  the harness runs with the frozen app's own Python. This install alone is
  for all users (``ALLUSERS=1``, Program Files), though people get a per-user
  install: the bench stage runs as SYSTEM, which has no person's profile to
  install into, and the install only provides the files to freeze. The
  frozen copy is byte-identical either way and runs from the bench's own
  folder, and the product is uninstalled again, so the install scope does
  not change what is measured. Without an MSI, ``bench prep`` builds the
  commit (the ``release`` stage) and freezes that build.

Per machine, in order
---------------------

1. **Candidate and harness.** With the candidate's commit checked out (a bench
   send carries the commit, and ``run`` refuses any other tree) and the
   candidate's MSI on the workstation::

      ANGELO_GCP_NAME=<machine> ANGELO_BENCH_CANDIDATE_MSI=<the MSI> \
        ANGELO_BENCH_MCP_RUNTIME=<the frozen MCP runtime> \
        scripts/angelo/gcp_windows_validate.sh bench prep

   It installs and freezes the candidate, then waits
   (``ANGELO_BENCH_SIGNIN_MINUTES``, 60) and says "READY FOR SIGN-IN". A
   machine signed in before keeps its sign-in: the stage checks the accounts
   its ``SIGNED-IN`` marker names again (``check-sign-in``, existence only),
   keeps the marker and ends, and ``bench prep`` says "SIGNED IN ALREADY"
   instead; an incomplete sign-in is asked for again. READY is said only when
   the stage is still waiting a minute after it posted ``SIGN-IN-READY``
   (``ANGELO_GCP_READY_SETTLE_SECONDS``, 60), and ``bench prep``, like every
   ``bench`` and ``wait-off``, exits 0 only when the stage's exit code (the
   bootstrap's ``DONE``) is 0: a stage that fails after READY is reported as
   "NOT READY" with its exit code.
2. **Sign-in**, once per machine (`Signing in`_), for the connections whose
   steps the machine runs.
3. ``bench dry``: every unbilled check of the campaign in a desktop session.
4. ``bench run --authorize-billed-runs``, with the machine's steps in
   ``ANGELO_BENCH_ONLY`` (globs separated by spaces, as ``run --only``) when
   it runs only part of the plan.
5. **Stopped.** ``bench run`` waits for the session's end, then confirms the
   machine is TERMINATED, stopping it if it is not; ``status`` must say so. A
   session ends after ``ANGELO_GCP_BENCH_HOURS`` (7): ``bench run`` again
   resumes the campaign. ``bench wait`` re-attaches to a running session.

Several machines
----------------

The steps of one plan can be spread over identical machines with
``ANGELO_BENCH_ONLY``, for example the gate steps and one model's
head-to-head on one machine, and each other model's head-to-head and
returning-user steps (``h2h-<model>-* returning-<model>-*``) on a machine of
its own. Two trials still never run at once on one machine. Run the gate
steps first and start the other machines only once every gate is at its bar:
a machine whose steps hold no gate cannot check it.

To make the other machines, stop the prepared machine after ``bench prep``
and an unbilled ``bench dry``, before any sign-in, and make a machine image of
it named ``<machine>-bench-base`` (``gcloud compute machine-images create``;
``ANGELO_GCP_IMAGE`` names another). It holds the tools, the MCP side and the
frozen candidate but no credential. Then, from the workstation::

   ANGELO_GCP_NAME=<machine> scripts/angelo/gcp_windows_validate.sh clone <clone>

Each clone gets its own bucket (``<project>-<clone>``: the command queue,
inputs and results never mix), readable by the original machine's service
account, which the image keeps; it boots once, finds nothing to do and powers
itself off. Then steps 1 to 5 with ``ANGELO_GCP_NAME=<clone>``. A sign-in is
never copied: each clone signs in itself.

Signing in
----------

While ``bench prep`` waits, from the workstation::

   gcloud compute reset-windows-password <machine> --user <operator> \
     --zone <zone> --project <project>
   gcloud compute start-iap-tunnel <machine> 3389 --local-host-port=localhost:13389 \
     --zone <zone> --project <project>

The first prints a password for a new local administrator ``<operator>`` to
you alone; keep it out of chats and files. Leave the tunnel running, connect
a Remote Desktop client to ``localhost:13389`` as ``<operator>`` with that
password, open PowerShell there and run::

   powershell -ExecutionPolicy Bypass -File C:\angelo\src\scripts\angelo\windows_final_runs.ps1 -SignIn

(add ``-Accounts chatgpt`` or ``-Accounts claude`` on a machine whose steps
use only one connection). It creates the bench's connections
(``final_runs.py prepare-connections``: one Claude and one ChatGPT
subscription, each with its own profile in ``C:\angelo\bench\connections``,
never the person's own) and then:

* **Claude Code** prints a sign-in URL: open it in a browser on the
  workstation (copy it across), sign in with the Claude subscription, and
  paste the code it shows back into the session.
* **Codex** prints a URL and a one-time code: open the URL in a browser
  signed in to the ChatGPT account the benchmark should use, and enter the
  code. If the page refuses device codes, allow them in ChatGPT's settings
  (Security) and run ``-SignIn`` again; finished sign-ins are kept.

It checks the sign-ins (``check-sign-in``, existence only) and writes
``SIGNED-IN`` with the accounts it checked; sign out of the session and stop
the tunnel, and the stage continues by itself. The sign-ins stay on that
machine, in Claude Code's and Codex's own files in the bench profiles
(Codex's kept in a file, not one Windows user's credential store); nothing
reads them, and none is ever copied to another machine. Angelo and the stock
Codex use the same ChatGPT profile, as on a workstation.

Results
-------

Each session ends by copying its campaign (every step's results,
``JOURNAL.jsonl``, ``STATE.json`` and the session's ``report`` as
``REPORT.json``) to ``gs://<project>-<machine>/bench/campaign-msi-<sha12>/``
(``<sha12>``: the first 12 hexadecimal digits of the MSI's SHA-256) and ``facts.json`` beside it. On the
workstation::

   gcloud storage rsync --recursive \
     gs://<project>-<machine>/bench/campaign-msi-<sha12> <results folder>/<machine>

and read each machine's ``REPORT.json``; the machines' folders together are
the campaign. Stop and resume as on a workstation (`Resume, stop and provider
limits`_): ``bench run`` again continues a machine's campaign where it
stopped.

Files
=====

All under ``tests/python/angelo_agent/``.

``final_runs.py``
   The driver (``freeze``, ``init``, ``plan``, ``dry-run``, ``headroom``,
   ``run``, ``status``, ``report``, ``check-sign-in``,
   ``prepare-connections``).
``final_runs_plan.py``
   The steps in run order, their commands' common options and their
   estimates.
``final_runs_load.py``
   The quiet-machine guard and the load sampler.
``final_runs_freeze.py``
   The candidate's freeze and its checks.
``final_runs_report.py``
   The report, the returning-user columns, the rate-limit read and the
   sign-in check.
``final_runs_mcp.py``
   Bundles the MCP side's frozen add-on and server with digests and a
   requirements file pinning every dependency, and installs the bundle on
   another machine, so both machines run byte-identical add-on and server
   code with version-identical dependencies.
``final_runs_sources.py``
   The one-shot tests' source pictures on another machine: the manifest is
   the tests' own SHA-256 pins; ``check`` and ``paths`` verify a folder
   against them (on the workstation before the upload, on the machine after
   the download).
``test_native_final_runs.py``
   Host tests, nothing launched: the order and commands, the gate bar,
   provider limits, the quiet-machine guard, resume and set-aside trials, the
   freeze, the MCP bundle and the refusals.
``test_native_windows_bench_stage.py`` and ``ps_stderr_lint.py``
   Host tests of the Windows stage's failure paths: ``wait-off`` against a
   stand-in ``gcloud`` (READY only for a live stage, the stage's exit code as
   the verdict, a kept sign-in), the kept-sign-in check, and a read of the
   VM's scripts (``windows_final_runs.ps1``, ``windows_bench_tools.ps1``,
   ``windows_cloud_bootstrap.ps1``) for a native call whose stderr is
   redirected while ``$ErrorActionPreference`` may be ``Stop``: Windows
   PowerShell 5.1 makes each such line an error record, and under Stop the
   first ends the script (gcloud's progress output on stderr would do so).
   Such calls run under ``Continue`` and check their exit code
   (``Invoke-Native``, ``Invoke-Gcloud``). Also what only the bench user's
   session shows at full dry-run depth: the stock Blender jobs' Windows
   profile folders and the MCP runtime's own interpreter.
``bench_totals.py``, ``list_prices.py``
   Totals that keep an unrecorded value unknown, each comparison's one cost
   basis, and list prices with the price step per request
   (``oneshot_metrics`` re-exports the latter).
``test_native_bench_unknown_totals.py``, ``test_native_bench_price_step.py``, ``test_native_bench_cost_basis.py``
   Host tests of them: unknown and partly recorded trials, sums of requests
   priced per request or refused, and one cost basis per comparison.
``test_native_final_runs_unknown_load.py``
   Host tests that an unreadable process list or load is never read as a
   quiet machine.
``bench_workdirs.py``, ``test_native_bench_workdirs.py``
   The runtimes' working folders, outside any checkout, and the refusal of a
   trial whose folder or client home would give it project context.
``test_native_bench_cache_ttl.py``
   Host tests of list prices by cache-write TTL: 1-hour, 5-minute, mixed,
   partial and missing splits, the split read from a recorded stream, and
   both sides of an embedded recorded Opus sample matching Claude Code's
   estimate.
   ``ANGELO_BENCH_RECORDED_RUN=<step folder>`` also checks every request of a
   recorded Claude head-to-head step.
