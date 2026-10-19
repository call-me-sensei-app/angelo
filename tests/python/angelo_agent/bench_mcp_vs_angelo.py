# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Opt-in, BILLED latency and correctness benchmark: Blender MCP + Claude Code vs Angelo.

Side A is Blender Lab's official MCP server (the installed Claude extension's own
virtualenv) driven by headless Claude Code against stock Blender with a private
copy of the MCP add-on (``bench_mcp_trial.py``). Side B is Angelo's native agent
through the unchanged live-eval harness (``eval_live_agent.py --trials 1``),
started once per trial. Both use the SAME Claude account (the connection's
native Claude Code profile), the same pinned model, ``--effort low``, Fast off,
the same prompts (``eval_live_tasks.build_tasks``) from the factory default-cube
scene, and are graded by the same host predicates. Trials alternate (MCP,
Angelo, MCP, Angelo...); every trial is a fresh process pair, scene and session.

Every task prompt is a billed message on that account: per trial 5 on the MCP
side and 6 on the Angelo side (its sixth, the staged preview, has no MCP
equivalent and is reported separately). Nothing is retried. The script prints
the plan, then refuses to run without ``--authorize-billed-runs`` and an
explicit ``--connection-id``. ``--dry-run-mcp`` proves the MCP-side launch,
isolation, server path and grading with no model at all.

Usage, from the repository root (host Python)::

    python3 tests/python/angelo_agent/bench_mcp_vs_angelo.py --plan-only
    python3 tests/python/angelo_agent/bench_mcp_vs_angelo.py --dry-run-mcp --output DIR
    python3 tests/python/angelo_agent/bench_mcp_vs_angelo.py --authorize-billed-runs \\
        --connection-id UUID --source-connections-dir DIR --angelo-binary PATH --output DIR
    # Rerun only Angelo (for example after a product fix) against earlier MCP trials that were sent and graded
    # exactly as the new ones (same task set, model, effort, prompts, references and test code: bench_integrity):
    python3 ... --sides angelo --reuse-mcp-results OLD_DIR --authorize-billed-runs ... --output NEW_DIR
    # Rebuild the JSON and Markdown from the raw trial folders without sending anything:
    python3 ... --summarize-only DIR
    # Scenario sets (scenario_specs): a multi-turn build against reference images, scored at the end.
    python3 ... --task-set scenario-bridge --dry-run-scenario --output DIR     # unbilled, scripted models
    python3 ... --task-set scenario-bridge --authorize-billed-runs --connection-id UUID ... --output DIR
    # One-shot tests (doc/angelo/oneshot_tests.rst): one message with reference images per side, then nothing.
    python3 ... --task-set oneshot-footbridge --dry-run-scenario --output DIR
    # Every mini one-shot test in turn, each its own one-message run in DIR/<task set> (oneshot_group):
    python3 ... --task-set oneshot-minis --authorize-billed-runs --connection-id UUID ... --output DIR

Never touches port 9876 (the Blender MCP add-on's default, left to an interactive pair), the original
add-on, the extension's virtualenv contents, or any credential file.
"""

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for _path in (str(ROOT / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import bench_addon_pin as addon_pin  # noqa: E402  (the add-on the Angelo side imports must be the app's)
import bench_cold_warm as cold_warm  # noqa: E402
import bench_integrity as integrity  # noqa: E402
import bench_mcp_codex as codex_mcp  # noqa: E402
import bench_mcp_gate as gate  # noqa: E402
from bench_mcp_method import (METHOD, METHOD_CODEX, METHOD_OPENING, METHOD_QUESTIONS, METHOD_SCENARIO,  # noqa: E402
                              THREATS, THREATS_OPENING)
import bench_mcp_report as report  # noqa: E402
import bench_mcp_trial as mcp  # noqa: E402
import bench_workdirs as workdirs  # noqa: E402
import eval_approval as approval  # noqa: E402
import eval_ask_why as ask_why  # noqa: E402
import eval_diagnostics as diagnostics  # noqa: E402
import eval_warm_up as warm_up  # noqa: E402  (--warm-up-only: one untimed, ungraded message per side, never a trial)
import eval_gate  # noqa: E402
import eval_live_tasks as live  # noqa: E402
import eval_provider_limits as limits  # noqa: E402
import gui_launch  # noqa: E402
from angelo_agent import product_env, session_journal  # noqa: E402  (standard library only)
import oneshot_mcp  # noqa: E402
import oneshot_metrics  # noqa: E402
import scenario_specs  # noqa: E402

HOME = Path.home()
RESULTS = "bench-mcp-vs-angelo.json"
RUN_PLAN = "RUN-PLAN.json"  # The plan a run started with; --resume continues only the same plan.
SUMMARY = "SUMMARY.md"
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_MODELS = {"claude": DEFAULT_MODEL, "codex": "gpt-6-astra"}
BUILD_FIELDS = ("Blender", "build date", "build hash", "build branch", "build type")
# --dry-run-scenario --sides: the dry run's own three sides (both MCP runtimes, then Angelo).
DRY_RUN_SIDES = {"both": ("mcp-claude", "mcp-codex", "angelo"), "mcp": ("mcp-claude", "mcp-codex"),
                 "angelo": ("angelo",)}


def macos_default(path, platform=None):
    """``path`` on macOS, where the benchmark's installs live there; elsewhere no default (preflight asks)."""
    return str(path) if (sys.platform if platform is None else platform) == "darwin" else ""


def parse(argv):
    parser = argparse.ArgumentParser(description="BILLED MCP vs Angelo benchmark (see module docstring)")
    parser.add_argument("--authorize-billed-runs", action="store_true",
                        help="required: confirm every planned prompt is billed to the selected Claude account")
    parser.add_argument("--connection-id", default="", help="required: the Angelo Claude subscription connection UUID")
    parser.add_argument("--source-connections-dir", default="", help="Angelo connection store (default: the app's)")
    parser.add_argument("--claude-config-dir", default="",
                        help="Claude Code profile for the MCP side (default: that connection's native profile)")
    parser.add_argument("--trials", type=int, default=5, help="trials per side, alternating; 1-20")
    parser.add_argument("--runtime", choices=("claude", "codex"), default="claude",
                        help="claude: Claude Code on the MCP side and a Claude connection on Angelo's; codex: "
                             "codex app-server on the MCP side (bench_mcp_codex) and a Codex connection on Angelo's")
    parser.add_argument("--model", default="", help="model ID pinned on BOTH sides (default: the runtime's)")
    parser.add_argument("--codex", default=codex_mcp.DEFAULT_CODEX, help="--runtime codex: the codex executable")
    parser.add_argument("--codex-home", default="",
                        help="--runtime codex: dedicated signed-in profile for BOTH sides, named "
                             ".../codex-profile/<connection id> (CODEX_HOME=<it> codex login); never ~/.codex")
    parser.add_argument("--angelo-team-mode", choices=("",) + live.TEAM_MODES, default="",
                        help="--sides angelo: run Angelo's side through the Angelo gateway with the signed-in team's "
                             "connection of this mode (eval_live_agent.py --team-mode); --source-connections-dir is "
                             "the dedicated team profile and --connection-id is not used")
    parser.add_argument("--effort", choices=("low", "medium", "high"), default="low")
    parser.add_argument("--task-timeout", type=float, default=None,
                        help="seconds per task before Stop (default 300; a one-shot test's own time cap)")
    parser.add_argument("--sides", choices=("both", "mcp", "angelo"), default="both")
    parser.add_argument("--angelo-binary", default=product_env.get("BINARY") or "",
                        help="compiled Angelo executable (default: $ANGELO_BINARY)")
    parser.add_argument("--stock-blender", default=macos_default("/Applications/Blender.app/Contents/MacOS/Blender"),
                        help="stock Blender for the MCP side (default on macOS: the installed Blender.app)")
    parser.add_argument("--mcp-server-dir", default=macos_default(
        HOME / "Library/Application Support/Claude/Claude Extensions/ant.dir.gh.blender.blender-mcp"),
                        help="the Claude extension's Blender MCP folder (default on macOS: the installed one)")
    parser.add_argument("--mcp-addon-dir",
                        default=macos_default(HOME / "Library/Application Support/Blender/5.2/extensions/user_default/mcp"),
                        help="the Blender MCP add-on to copy (default on macOS: stock Blender's installed one)")
    parser.add_argument("--claude", default=str(HOME / ".local/bin/claude"))
    parser.add_argument("--output", default="", help="new or empty results directory")
    parser.add_argument("--reuse-mcp-results", default="",
                        help="earlier output directory whose MCP trials are compared; refused unless they had this "
                             "run's task set, runtime, model, effort, prompt SHA-256s, references and test code "
                             "(harness and product graders) and came from no diagnostic run (bench_integrity)")
    parser.add_argument("--cross-model-reuse", action="store_true",
                        help="with --reuse-mcp-results: allow MCP trials of another model or effort (never another "
                             "runtime) for a tier measurement; the run is labelled and never a gate or measured figure")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--preflight-only", action="store_true",
                        help="run every check of a billed start (executables, sign-ins, --output) and stop; unbilled")
    parser.add_argument("--dry-run-mcp", action="store_true", help="no model: launch, MCP server path and grading only")
    parser.add_argument("--summarize-only", default="", help="rebuild results from an output directory; sends nothing")
    # The delegation set has no MCP equivalent; run it with eval_live_agent.py.
    parser.add_argument("--task-set", default="default",
                        choices=("default", "long") + scenario_specs.TASK_SETS + scenario_specs.ONESHOT_TASK_SETS,
                        help="default: the five head-to-head tasks (plus Angelo's preview); long: one long chat; "
                             "scenario-<id>: a multi-turn build against reference images (scenario_specs); "
                             "oneshot-<id>: one message, then nothing (oneshot_tests.rst); oneshot-minis runs "
                             "every mini one-shot test in turn")
    parser.add_argument("--cost-cap", type=float, default=0.,
                        help="one-shot tests: stop a side once its model usage passes this many USD at list prices "
                             "(default: the test's own cap)")
    parser.add_argument("--python-approvals", choices=scenario_specs.PYTHON_APPROVAL_MODES, default="approve",
                        help="scenario sets, Angelo side: approve (default) or decline reviewed Python")
    parser.add_argument("--dry-run-scenario", action="store_true",
                        help="scenario sets: the whole run with scripted models on both MCP runtimes and Angelo; "
                             "unbilled, no account")
    parser.add_argument("--demo-record", action="store_true",
                        help="scenario sets: record Angelo's trial window as a 1080p H.264 demo video and poster "
                             "(scenario_demo); recording costs main-thread time, so Angelo's timings are flagged")
    parser.add_argument("--stage-copies", action="store_true",
                        help="scenario sets, Angelo side: keep a copy of the scene after every turn, for stage renders")
    parser.add_argument("--render-device", choices=("", "cpu", "gpu"), default="",
                        help="scenario sets, Angelo side: gpu enables the machine's GPU for Cycles in the trial's "
                             "preferences (default: the factory preference, CPU)")
    parser.add_argument("--long-tasks", type=int, default=live.LONG_SESSION_TASKS, help="tasks in the long set; 2-120")
    parser.add_argument("--angelo-observation", choices=("legacy", "turn-context"), default="legacy",
                        help="Angelo's per-message observation; legacy matches earlier runs")
    parser.add_argument("--angelo-context-rebase-tokens", type=int, default=0,
                        help="Angelo's context rebase threshold; 0 (the product default) is off")
    gui_launch.add_arguments(parser, both_sides=True)   # --input-isolation and --window-state
    parser.add_argument("--after-failure", choices=live.AFTER_FAILURE_MODES, default="skip",
                        help="both sides: skip (default, comparable with earlier runs) or reset, which restores each "
                             "task's reference state so every task of a trial is graded")
    parser.add_argument("--angelo-cache-ttl", choices=("", "5m", "1h"), default="",
                        help="prompt-cache TTL of Angelo's new Claude sessions; default: the product's choice")
    parser.add_argument("--angelo-trusted-scripts", action="store_true",
                        help="Angelo side: the chat's Trusted scripts on (scene Python runs without a review), matching "
                             "the MCP side, whose scripts run without a prompt")
    parser.add_argument("--angelo-always-allow-scripts", action="store_true",
                        help="Angelo side: the person's Always allow scripts preference on in the trial's profile (scene "
                             "Python runs without a review in Auto chats), the opted-in product path")
    parser.add_argument("--angelo-approval", choices=("default", "ask"), default="default",
                        help="Angelo side: default keeps each new chat's own approval (Auto · bounded edits); ask puts "
                             "the chats in Ask (eval_live_agent.py --approval)")
    parser.add_argument("--resume", action="store_true",
                        help="continue an interrupted run in --output: completed trials are kept and skipped, a trial "
                             "cut short is moved to interrupted/ (kept) and run again; the plan must be unchanged")
    parser.add_argument("--max-new-trials", type=int, default=0,
                        help="run at most this many new trial numbers, then summarize and exit (0: all); with "
                             "--resume a caller can check the machine between trials")
    parser.add_argument("--angelo-stable-workspace", action="store_true",
                        help="run every Angelo trial's fresh state at one path, as the MCP side reuses one "
                             "working directory, so both sides' Claude working directories are stable")
    parser.add_argument("--angelo-opening-wait", type=float, default=0.0, metavar="SECONDS",
                        help="a labelled extra, not the published cold-start method: Angelo readies its chat's opening "
                             "(its Codex app-server and base) when the chat is bound and the first prompt waits for it "
                             "up to SECONDS; the MCP side gets no pre-start, and the result's method and threats say so")
    parser.add_argument("--returning-user", action="store_true",
                        help="Codex runtime: both sides as a returning user. Angelo keeps one profile across its "
                             "trials (angelo-profile/: state with the remembered Codex bases, user folders, connection "
                             "copy), opens a new chat per task and logs every request; the MCP side, a new launch per "
                             "trial as before, opens a new Codex thread per task. Trial 1's first task is the cold start")
    diagnostics.add_arguments(parser)   # --debug-thinking and --ask-why-on-miss: diagnostic runs, never claims.
    addon_pin.add_argument(parser)   # --allow-addon-mismatch
    warm_up.add_arguments(parser)
    args = approval.check(parser, parser.parse_args(argv), prefix="angelo_")  # Before any billed trial.
    diagnostics.check(parser, args)
    warm_up.check(parser, args)
    if args.cross_model_reuse and not args.reuse_mcp_results:
        parser.error("--cross-model-reuse needs --reuse-mcp-results")
    if args.returning_user and args.runtime != "codex":
        parser.error("--returning-user measures Codex's remembered bases: use --runtime codex")
    args.model = args.model or DEFAULT_MODELS[args.runtime]
    scenario, oneshot = scenario_specs.is_scenario(args.task_set), scenario_specs.is_oneshot(args.task_set)
    args.task_timeout = args.task_timeout or scenario_specs.wall_cap(args.task_set) or 300.
    if args.cost_cap and not oneshot:
        parser.error("--cost-cap needs a one-shot task set")
    if (args.dry_run_scenario or args.demo_record or args.stage_copies or args.render_device) and not scenario:
        parser.error("--dry-run-scenario, --demo-record, --stage-copies and --render-device need a scenario task set")
    if scenario and args.after_failure != "skip":
        parser.error("scenario sets build on every earlier turn's real result: use --after-failure skip")
    if not 1 <= args.trials <= 20:
        parser.error("--trials must be 1-20")
    longest = scenario_specs.ONESHOT_MAX_WALL_S if oneshot else 1800   # One turn carries a one-shot test's whole build.
    if not 30 <= args.task_timeout <= longest:
        parser.error("--task-timeout must be 30-%d seconds" % longest)
    if not 2 <= args.long_tasks <= 120:
        parser.error("--long-tasks must be 2-120")
    if not 0 <= args.max_new_trials <= 20:
        parser.error("--max-new-trials must be 0-20")
    return args


def tasks(args):
    """The configured task list (the same function the in-app driver and the grading use).

    ``args.gate_version`` is set only when an earlier run is summarized again: its own gate-set version.
    """
    return live.tasks_for(getattr(args, "task_set", "default"), getattr(args, "long_tasks", live.LONG_SESSION_TASKS),
                          getattr(args, "gate_version", None))


def window_states(args):
    """Each side's window state (gui_launch). A run summarized again keeps the states it recorded, and reused MCP
    results keep the state their run recorded; one recorded before the state was part of the plan says so."""
    def recorded(folder):
        path = Path(folder) / RUN_PLAN
        return json.loads(path.read_text()).get("window_state", "not recorded") if path.is_file() else "not recorded"
    if getattr(args, "summarize_only", ""):
        return recorded(args.summarize_only)
    states = {side: gui_launch.side_window_state(side, getattr(args, "window_state", "behind")) for side in ("mcp", "angelo")}
    if getattr(args, "reuse_mcp_results", ""):
        reused = recorded(args.reuse_mcp_results)
        states["mcp"] = reused.get("mcp", "not recorded") if isinstance(reused, dict) else reused
    return states


def window_asymmetry(args):
    """A line saying the sides' window states differ (their timings are then not comparable), or ''."""
    states = window_states(args)
    sides = ("mcp", "angelo") if args.sides == "both" or getattr(args, "reuse_mcp_results", "") else (args.sides,)
    if not isinstance(states, dict) or len({states.get(side) for side in sides}) < 2:
        return ""
    return ("Window states differ between the sides (%s); their timings are not comparable. Timed comparisons give "
            "both sides the same state (--window-state front on macOS)." % "; ".join("%s %s" % (side, states[side])
                                                                                    for side in sides))


def plan(args):
    order = [(side, trial) for trial in range(1, args.trials + 1) for side in ("mcp", "angelo")
             if args.sides in ("both", side)]
    head = [task["id"] for task in tasks(args) if task["kind"] != "preview"]
    return {"order": ["%s-%d" % item for item in order], "runtime": getattr(args, "runtime", "claude"),
            "model": args.model, "effort": args.effort, "fast": False,
            "task_set": getattr(args, "task_set", "default"),
            "gate_version": eval_gate.set_version(getattr(args, "task_set", "default"), tasks(args)),
            "input_isolation": getattr(args, "input_isolation", "on"), "after_failure": getattr(args, "after_failure", "skip"),
            "window_state": window_states(args),
            "refusal_fallback": "disabled",
            "rate_limits": "a provider rate or usage limit makes its trial invalid (never a task failure) and "
                           "stops the run; invalid trials are left out of every pass rate and counted",
            "angelo": {"observation": getattr(args, "angelo_observation", "legacy"),
                       "trusted_scripts": bool(getattr(args, "angelo_trusted_scripts", False)),
                       "approval": getattr(args, "angelo_approval", "default"),
                       "always_allow_scripts": bool(getattr(args, "angelo_always_allow_scripts", False)),
                       "context_rebase_tokens": getattr(args, "angelo_context_rebase_tokens", 0),
                       "cache_ttl": getattr(args, "angelo_cache_ttl", "") or "product default",
                       "stable_workspace": bool(getattr(args, "angelo_stable_workspace", False)),
                       "returning_user": bool(getattr(args, "returning_user", False)),
                       "opening_wait_s": getattr(args, "angelo_opening_wait", 0.0)},
            **({"mcp": {"new_thread_per_task": True}} if getattr(args, "returning_user", False) else {}),
            "mcp_tasks": head, "angelo_tasks": [task["id"] for task in tasks(args)],
            **({"scenario": scenario_specs.plan(scenario_specs.scenario_id(args.task_set)),
                "python_approvals": getattr(args, "python_approvals", "approve"),
                "demo_record": bool(getattr(args, "demo_record", False)),
                "stage_copies": bool(getattr(args, "stage_copies", False)),
                "render_device": getattr(args, "render_device", "") or "factory preference"}
               if scenario_specs.is_scenario(getattr(args, "task_set", "default")) else {}),
            **oneshot_mcp.plan(args),
            **diagnostics.plan(args, ask_why.MCP_SIDE[getattr(args, "runtime", "claude")]
                               if getattr(args, "sides", "both") != "angelo" else None),
            **reuse_plan(args),
            "billed_prompts": {"mcp": len(head) * sum(s == "mcp" for s, _ in order),
                               "angelo": len(tasks(args)) * sum(s == "angelo" for s, _ in order)},
            "billing": "Each prompt is one billed message on the selected %s account; a message can contain "
                       "several model calls. Failed tasks are recorded, never retried."
                       % ("ChatGPT" if getattr(args, "runtime", "claude") == "codex" else "Claude")}


def reuse_plan(args):
    """The reused MCP folder's identity in the plan, and a cross-model reuse's label (nothing without reuse)."""
    if not getattr(args, "reuse_mcp_results", ""):
        return {}
    folder = Path(args.reuse_mcp_results).resolve()
    earlier = integrity.recorded_plan(folder) or {}
    entry = {"reused_mcp": {"folder": str(folder), "run_plan_sha256": integrity.prompt_sha256(
        json.dumps(earlier, sort_keys=True)) if earlier else None}}
    if getattr(args, "cross_model_reuse", False):
        entry["cross_model_reuse"] = {
            "mcp": {key: earlier.get(key) for key in ("model", "effort")},
            "angelo": {"model": args.model, "effort": args.effort},
            "claim": "a tier measurement: never a gate, timing or benchmark figure"}
    return entry


def preflight(args):
    """Every refusal happens here, before any process or billed message."""
    if getattr(args, "angelo_team_mode", ""):
        if args.sides != "angelo" or args.connection_id:
            return (["--angelo-team-mode runs only --sides angelo, without --connection-id"],
                    Path(args.output).resolve() if args.output else None)
        args.connection_id = live.TEAM_PREFIX + args.angelo_team_mode
    # --preflight-only sends nothing, so it checks as an authorized run would.
    problems = [] if args.dry_run_mcp else live.refusal_reasons(args.authorize_billed_runs or args.preflight_only,
                                                                 args.connection_id)
    if not args.source_connections_dir:
        from angelo_agent.storage import connection_directory
        args.source_connections_dir = str(connection_directory())
    codex = getattr(args, "runtime", "claude") == "codex"
    if not args.claude_config_dir and args.connection_id and not codex:
        args.claude_config_dir = str(Path(args.source_connections_dir) / "claude-profile" / args.connection_id)
    server = mcp.server_executable(args.mcp_server_dir) if args.mcp_server_dir else ""
    executables = {"--stock-blender": args.stock_blender, "--mcp-server-dir": server}
    if not codex:
        executables["--claude"] = args.claude
    elif args.dry_run_mcp:
        executables["--codex"] = args.codex
    elif args.sides != "angelo":  # The MCP side's Codex profile; an Angelo-only run (team mode too) never uses it.
        problems += codex_mcp.preflight(mcp_config(args, Path(args.output or ".").resolve()))
    if args.sides != "mcp" and not args.dry_run_mcp:
        executables["--angelo-binary"] = args.angelo_binary
    for flag, path in executables.items():
        if args.sides == "angelo" and flag != "--angelo-binary":
            continue
        if not path and flag == "--angelo-binary":
            problems.append("--angelo-binary is required here: pass it or set ANGELO_BINARY")
        elif not path:
            problems.append("%s is required here: it has no default on this platform (see --help)" % flag)
        elif not (Path(path).is_file() and os.access(path, os.X_OK)):
            problems.append("%s: no executable at %s" % (flag, path))
    if args.sides != "angelo" and not args.mcp_addon_dir:
        problems.append("--mcp-addon-dir is required here: it has no default on this platform (see --help)")
    elif args.sides != "angelo" and not (Path(args.mcp_addon_dir) / "blender_manifest.toml").is_file():
        problems.append("--mcp-addon-dir has no blender_manifest.toml")
    if args.sides != "angelo" and not args.dry_run_mcp and not codex and not Path(args.claude_config_dir or "/nonexistent").is_dir():
        problems.append("--claude-config-dir does not exist: %s" % args.claude_config_dir)
    if args.sides != "angelo" and args.output:   # Where the MCP side's client would read project context.
        config = mcp_config(args, Path(args.output).resolve())
        problems += workdirs.problems(config["claude_cwd"], config["codex_home" if codex else "claude_config_dir"],
                                      "codex" if codex else "claude")
    if args.reuse_mcp_results and args.sides != "angelo":
        problems.append("--reuse-mcp-results is only for --sides angelo")
    elif args.reuse_mcp_results:   # Only trials sent and graded exactly as this run's are paired (bench_integrity).
        problems += ["--reuse-mcp-results: " + problem for problem in integrity.reuse_problems(
            Path(args.reuse_mcp_results).resolve(), plan(args), tasks(args),
            cross_model=getattr(args, "cross_model_reuse", False))]
    output = Path(args.output).resolve() if args.output else None
    if output is None:
        problems.append("pass --output with a new or empty directory")
    elif output.exists() and any(output.iterdir()) and not getattr(args, "resume", False):
        problems.append("--output must be new or empty: %s" % output)
    elif getattr(args, "resume", False) and (output / RUN_PLAN).is_file():
        if json.loads((output / RUN_PLAN).read_text()) != json.loads(json.dumps(plan(args))):
            problems.append("--resume: the plan differs from the interrupted run's (%s); conditions would mix"
                            % (output / RUN_PLAN))
    return problems, output


def mcp_config(args, output):
    return {"stock_blender": args.stock_blender, "mcp_server_dir": args.mcp_server_dir,
            "mcp_addon_dir": args.mcp_addon_dir, "claude": args.claude, "claude_config_dir": args.claude_config_dir,
            "claude_cwd": str(workdirs.root(output) / "mcp-cwd"), "model": args.model, "effort": args.effort,
            "task_timeout": args.task_timeout, "task_set": getattr(args, "task_set", "default"),
            "long_tasks": getattr(args, "long_tasks", live.LONG_SESSION_TASKS),
            "input_isolation": getattr(args, "input_isolation", "on"), "after_failure": getattr(args, "after_failure", "skip"),
            "window_state": getattr(args, "window_state", "behind"),
            "runtime": getattr(args, "runtime", "claude"), "codex": getattr(args, "codex", codex_mcp.DEFAULT_CODEX),
            "codex_home": getattr(args, "codex_home", ""), "cost_cap": getattr(args, "cost_cap", 0),
            "new_thread_per_task": bool(getattr(args, "returning_user", False)), **diagnostics.stamp(args)}


def job_wait(args):
    """Seconds a scenario set may wait for its background job (0 for every other task set)."""
    task_set = getattr(args, "task_set", "default")
    return scenario_specs.job_wait(scenario_specs.scenario_id(task_set)) if scenario_specs.is_scenario(task_set) else 0


def transcript_slug(path):
    """Claude Code's per-project folder name: every non-alphanumeric character becomes '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def angelo_command(args, output, folder):
    """``(command, workspace)``: the live eval's command for one Angelo trial (or warm-up) writing to ``folder``."""
    team = getattr(args, "angelo_team_mode", "")
    command = [sys.executable, str(HERE / "eval_live_agent.py"), "--authorize-billed-runs",
               *(("--team-mode", team) if team else ("--connection-id", args.connection_id)),
               "--source-connections-dir", args.source_connections_dir,
               "--blender", args.angelo_binary, "--effort", args.effort, "--model", args.model, "--trials", "1",
               "--task-timeout", "%g" % args.task_timeout, "--output", str(folder),
               "--task-set", getattr(args, "task_set", "default"),
               "--long-tasks", str(getattr(args, "long_tasks", live.LONG_SESSION_TASKS)),
               "--observation", getattr(args, "angelo_observation", "legacy"),
               "--context-rebase-tokens", str(getattr(args, "angelo_context_rebase_tokens", 0)),
               "--input-isolation", getattr(args, "input_isolation", "on"),
               "--window-state", getattr(args, "window_state", "behind"),
               "--after-failure", getattr(args, "after_failure", "skip")]
    command += [addon_pin.OVERRIDE] if getattr(args, "allow_addon_mismatch", False) else []
    if scenario_specs.is_scenario(getattr(args, "task_set", "default")):
        command += ["--python-approvals", getattr(args, "python_approvals", "approve")]
        command += ["--demo-record"] if getattr(args, "demo_record", False) else []
        command += ["--stage-copies"] if getattr(args, "stage_copies", False) else []
        command += ["--render-device", args.render_device] if getattr(args, "render_device", "") else []
        command += ["--cost-cap", "%g" % args.cost_cap] if getattr(args, "cost_cap", 0) else []
    if getattr(args, "angelo_cache_ttl", ""):
        command += ["--cache-ttl", args.angelo_cache_ttl]
    if getattr(args, "angelo_trusted_scripts", False):
        command += ["--trusted-scripts"]
    if getattr(args, "angelo_always_allow_scripts", False):
        command += ["--always-allow-scripts"]
    if getattr(args, "angelo_approval", "default") != "default":
        command += ["--approval", args.angelo_approval]
    command += diagnostics.arguments(diagnostics.stamp(args))   # Diagnostic runs only.
    if getattr(args, "runtime", "claude") == "codex" and not team:
        command += ["--codex-home", args.codex_home]
    workspace = workdirs.state_path(folder / "trial-01") / "workspace"   # Outside a checkout (bench_workdirs).
    if getattr(args, "angelo_opening_wait", 0.0) > 0:
        command += ["--opening-wait", "%g" % args.angelo_opening_wait]  # The labelled extra (METHOD_OPENING).
    if getattr(args, "returning_user", False):
        # One profile across the trials, as a returning artist's (eval_returning_user); the first launch is cold.
        command += ["--profile-dir", str(workdirs.root(output) / "angelo-profile"), "--new-chat-per-task",
                    "--request-log"]
        workspace = workdirs.root(output) / "angelo-profile" / "state" / "workspace"
    elif getattr(args, "angelo_stable_workspace", False):
        # One path for every trial (moved into the trial folder afterwards), like the MCP side's one cwd.
        command += ["--stable-state-dir", str(workdirs.root(output) / "angelo-state")]
        workspace = workdirs.root(output) / "angelo-state" / "workspace"
    return command, workspace


def run_angelo_trial(args, output, trial):
    """The unchanged live-eval harness, one trial; its receipts and transcript become records."""
    folder, tmp = output / ("angelo-trial-%02d" % trial), output / ("angelo-trial-%02d-tmp" % trial)
    tmp.mkdir()
    command, workspace = angelo_command(args, output, folder)
    codex = getattr(args, "runtime", "claude") == "codex"
    result = {"side": "angelo", "trial": trial, "harness_command": command,
              "addon": addon_pin.stamp(Path(args.angelo_binary).resolve()),   # As the trial starts.
              "load_average_start": (list(os.getloadavg()) if hasattr(os, "getloadavg") else None), "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    started, since = time.monotonic(), time.time()
    with (output / ("angelo-trial-%02d.log" % trial)).open("w") as log:
        try:
            result["exit_code"] = subprocess.run(command, cwd=ROOT, env=mcp.clean_environment(TMPDIR=tmp), stdout=log,
                                                 stderr=subprocess.STDOUT,
                                                 timeout=len(tasks(args)) * (2 * args.task_timeout + 60) + 300
                                                 + job_wait(args) + diagnostics.extra_seconds(args)).returncode
        except subprocess.TimeoutExpired:
            result["exit_code"] = "timeout"
    result.update(elapsed_s=round(time.monotonic() - started, 3), load_average_end=(list(os.getloadavg()) if hasattr(os, "getloadavg") else None))
    if codex and getattr(args, "codex_home", ""):   # Its per-request record (reasoning tokens, summaries, item times).
        result["rollouts"] = copy_codex_rollouts(args.codex_home, since, output / ("angelo-trial-%02d-rollouts" % trial),
                                                 diagnostics.stamp(args))
    # Codex threads keep their rollouts in the profile; the receipts carry the per-task numbers.
    source = None if codex else Path(args.claude_config_dir) / "projects" / transcript_slug(workspace)
    sessions = sorted(source.glob("*.jsonl"), key=lambda p: p.stat().st_mtime) if source and source.is_dir() else []
    if sessions:  # Read-only use of the profile: a copy is kept with the results.
        # The graded turns only; an ask-why follow-up goes to its own file (eval_ask_why.split_transcript).
        copied = output / ("angelo-trial-%02d-transcript.jsonl" % trial)
        ask_why.split_transcript(sessions[-1], copied, output / (ask_why.WHY_TRANSCRIPT % trial))
        for path in (copied, output / (ask_why.WHY_TRANSCRIPT % trial)):
            diagnostics.mark_transcript(path, diagnostics.stamp(args))
    return result


def copy_codex_rollouts(codex_home, since, target, stamp=None):
    """Angelo's Codex rollouts written since ``since`` (epoch seconds), copied read-only beside its trial or warm-up.

    The rollout is a Codex chat's per-request record: each response's token usage with its reasoning tokens
    (``token_usage_record``), the reasoning items with their start and end times and summaries, and the tool calls.
    Trials run one at a time, and the MCP side's threads are ephemeral (no rollout), so every rollout written during
    an Angelo trial is that trial's. Returns the copied file names.
    """
    sessions = Path(codex_home) / "sessions"
    found = sorted((path for path in sessions.rglob("rollout-*.jsonl") if path.stat().st_mtime >= since),
                   key=lambda path: path.name) if sessions.is_dir() else []
    for path in found:
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target / path.name)
        diagnostics.mark_transcript(target / path.name, stamp or {})
    return [path.name for path in found]


def mark_mcp_streams(args, output, trial):
    """A diagnostic run's MCP raw streams open with its mark (eval_diagnostics.mark_transcript); else nothing."""
    for name in ("claude-stream.jsonl", "codex-stream.jsonl", ask_why.WHY_STREAM):
        diagnostics.mark_transcript(output / ("mcp-trial-%02d" % trial) / name, diagnostics.stamp(args), stream=True)


def trial_done(output, side, trial):
    """Whether this side's trial finished in an earlier run: its receipt is there and no provider limit voided it.

    A rate-limited trial (invalid, never graded) is not done: ``--resume`` keeps it under interrupted/ and runs
    it again once the limit has reset.
    """
    receipt = (output / ("mcp-trial-%02d" % trial) / "trial.json" if side == "mcp"
               else output / ("angelo-trial-%02d" % trial) / "eval-live-agent.json")
    if not receipt.is_file():
        return False
    try:
        data = json.loads(receipt.read_text())
    except (OSError, ValueError):
        return False
    report = data if side == "mcp" else ((data.get("trials") or [{}])[0] if isinstance(data, dict) else {})
    return isinstance(report, dict) and report.get("invalid") != limits.INVALID


def set_aside(output, side, trial, into="interrupted"):
    """A trial an interruption cut short is kept under interrupted/ (or ``into``), never deleted, and runs again."""
    stem = "%s-trial-%02d" % (side, trial)
    leftovers = sorted(output.glob(stem + "*"))
    for state in {(output / "angelo-state").resolve(), (workdirs.root(output) / "angelo-state").resolve()}:
        if side == "angelo" and state.exists():   # --angelo-stable-workspace: the cut-short trial's live state.
            leftovers.append(state)
    if not leftovers:
        return []
    target = output / into / ("%s-%s" % (stem, time.strftime("%Y%m%d-%H%M%S")))
    target.mkdir(parents=True)
    for path in leftovers:
        shutil.move(str(path), str(target / path.name))
    return [path.name for path in leftovers]


def recorded_limit(folder):
    """A provider limit in a recorded Angelo trial's chats (for runs made before live detection), or None."""
    for session in sorted((folder / "trial-01" / "state" / "sessions").glob("*.json")):
        try:
            rows = session_journal.read(session)[0].get("messages") or []  # A chat file's base and records.
        except (OSError, ValueError):
            continue
        evidence = limits.angelo_limit(rows, None)  # Error rows only: no usage to pair other rows with.
        if evidence:
            return evidence
    return None


def load_angelo_trial(output, trial, task_list=None):
    harness = json.loads((output / ("angelo-trial-%02d" % trial) / "eval-live-agent.json").read_text())
    trial_report = (harness.get("trials") or [{}])[0]
    if not trial_report.get("invalid"):
        evidence = recorded_limit(output / ("angelo-trial-%02d" % trial))
        if evidence:
            trial_report.update(invalid=limits.INVALID, aborted=limits.STOPPED_MESSAGE % evidence)
    transcript = output / ("angelo-trial-%02d-transcript.jsonl" % trial)
    task_list = live.build_tasks() if task_list is None else task_list
    steps = report.angelo_transcript_steps(transcript, task_list) if transcript.is_file() else {}
    return trial_report, report.angelo_records(trial_report, trial, steps)


def load_mcp_trial(output, trial):
    """A recorded MCP trial, its step analysis recomputed from the raw stream (sends nothing)."""
    folder = output / ("mcp-trial-%02d" % trial)
    data = json.loads((folder / "trial.json").read_text())
    if data.get("runtime") == "codex":
        return _reanalyze_codex(folder, data)
    stream = folder / "claude-stream.jsonl"
    if stream.is_file():
        lines = [json.loads(line) for line in stream.read_text(encoding="utf-8").splitlines() if line.strip()]
        for record in data["tasks"]:
            if "sent_at_s" in record:
                events = [(line["t_s"], line["message"]) for line in lines if line["task"] == record["task"]]
                record.update(report.analyze_mcp_events(events, record["sent_at_s"]))
                # Cache writes per TTL, for a trial recorded before its usage kept them (list prices).
                record["provider_usage"] = oneshot_metrics.with_write_split(record.get("provider_usage"), events)
                result = next((message for _, message in reversed(events) if message.get("type") == "result"), None)
                # A waited-out throttle counts only for a trial recorded since Angelo's side counts it too.
                evidence = limits.mcp_limit(events, result, record.get("provider_usage"),
                                            bool(data.get("test_code")))
                if evidence and not data.get("invalid"):  # A run made before live detection.
                    record.update(status="invalid", invalid=limits.INVALID)
                    data.update(invalid=limits.INVALID, aborted=limits.STOPPED_MESSAGE % evidence)
    return data


def _reanalyze_codex(folder, data):
    """A recorded Codex MCP trial, its steps recomputed from the raw notifications (sends nothing)."""
    stream = folder / "codex-stream.jsonl"
    if not stream.is_file():
        return data
    lines = [json.loads(line) for line in stream.read_text(encoding="utf-8").splitlines() if line.strip()]
    for record in data["tasks"]:
        if "sent_at_s" not in record:
            continue
        events = [(line["t_s"], line["message"]) for line in lines if line["task"] == record["task"]]
        analysis = codex_mcp.analyze_codex_events(events, record["sent_at_s"])
        record.update({key: value for key, value in analysis.items() if key != "rate_limits"})
        completed = next((message.get("params") for _, message in reversed(events)
                          if message.get("method") == "turn/completed"), None)
        evidence = limits.codex_limit([message for _, message in events], completed, bool(data.get("test_code")))
        if evidence and not data.get("invalid"):
            record.update(status="invalid", invalid=limits.INVALID)
            data.update(invalid=limits.INVALID, aborted=limits.STOPPED_MESSAGE % evidence)
    return data


def gather(output, trials, reuse=None, task_list=None, returning=False):
    """Every trial's records and meta. ``returning`` (``--returning-user``): trials after the first are relaunches
    on a kept profile, so their first tasks are returning-user starts, not cold starts (``bench_cold_warm``)."""
    records, trial_meta = [], []
    for trial in range(1, trials + 1):
        source = reuse or output
        if (source / ("mcp-trial-%02d" % trial) / "trial.json").is_file():
            data = load_mcp_trial(source, trial)
            ran = integrity.recorded_plan(source) or {}
            for record in data["tasks"]:   # Priced by the runtime and model it ran with (a reused run's own).
                record.setdefault("runtime", data.get("runtime") or ran.get("runtime") or "claude")
                if reuse is not None and ran.get("model"):
                    record.setdefault("model", ran["model"])
            if data.get("invalid"):
                for record in data["tasks"]:
                    record["invalid"] = data["invalid"]
            records += cold_warm.mark_mcp(source / ("mcp-trial-%02d" % trial), data,   # Its first request too.
                                          cold_warm.launch_of(trial, returning))
            stamp = source / ("mcp-trial-%02d-source.json" % trial)   # The checkout that ran it.
            trial_meta.append(dict({k: v for k, v in data.items() if k != "tasks"},
                                   source=json.loads(stamp.read_text()) if stamp.is_file() else None))
        if (output / ("angelo-trial-%02d" % trial) / "eval-live-agent.json").is_file():
            trial_report, angelo = load_angelo_trial(output, trial, task_list)
            if trial_report.get("invalid"):
                for record in angelo:
                    record["invalid"] = trial_report["invalid"]
            records += cold_warm.mark_angelo(output, trial, trial_report, angelo, cold_warm.launch_of(trial, returning))
            launcher = output / ("angelo-trial-%02d-launch.json" % trial)
            meta = json.loads(launcher.read_text()) if launcher.is_file() else {"side": "angelo", "trial": trial}
            meta.update(version=trial_report.get("version"), binary=trial_report.get("binary"),
                        aborted=trial_report.get("aborted"), success=trial_report.get("success"),
                        invalid=trial_report.get("invalid"), scenario=trial_report.get("scenario"),
                        addon=trial_report.get("addon") or meta.get("addon"))   # What the trial imported.
            trial_meta.append(meta)
    return records, trial_meta


def _run(command, **kwargs):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=60, **kwargs).stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as error:
        return "unavailable: %s" % error


def versions(args, output, records, trial_meta):
    git = lambda *a: _run(["git", "-C", str(ROOT), *a], env=dict(mcp.clean_environment(), GIT_LFS_SKIP_SMUDGE="1"))
    scratch = output / "version-probe"
    scratch.mkdir(exist_ok=True)
    blender_env = mcp.clean_environment(BLENDER_USER_RESOURCES=scratch, TMPDIR=scratch)
    manifest = Path(args.mcp_server_dir) / "manifest.json" if args.mcp_server_dir else None
    models = {side: sorted({m for r in records if r["side"] == side for m in
                            (r.get("models") or (r.get("result") or {}).get("models") or [])}) for side in report.SIDES}
    inits = [r["init"] for r in records if r.get("init")]
    codex = getattr(args, "runtime", "claude") == "codex"
    return {
        "runtime": getattr(args, "runtime", "claude"),
        "claude_code": None if codex else _run([args.claude, "--version"], env=mcp.clean_environment()),
        "codex": _run([args.codex, "--version"], env=mcp.clean_environment()) if codex else None,
        "codex_account": next((m.get("account") for m in trial_meta if m.get("account")), None) if codex else None,
        "models_reported": models, "requested_model": args.model, "effort": args.effort, "fast_mode": False,
        "mcp_init_model": sorted({i.get("model") for i in inits if i.get("model")}),
        "mcp_server": {"extension_manifest_version": json.loads(manifest.read_text()).get("version")
                       if manifest and manifest.is_file() else None, "path": args.mcp_server_dir},
        "mcp_addon": next((m.get("blender", {}).get("addon_version") for m in trial_meta if m.get("blender")), None),
        "stock_blender": next(({k: m["blender"].get(k) for k in ("blender_version", "build_hash")}
                               for m in trial_meta if m.get("blender")), None),
        "angelo_binary": [line.strip() for line in _run([args.angelo_binary, "--version"], env=blender_env).splitlines()
                          if line.strip().startswith(BUILD_FIELDS)]
                         if args.angelo_binary and Path(args.angelo_binary).is_file() else None,
        "angelo_checkout_commit": git("rev-parse", "HEAD"), "angelo_checkout_branch": git("branch", "--show-current"),
        "angelo_checkout_dirty_files": len([line for line in git("status", "--porcelain").splitlines() if line]),
        "angelo_python_source": "the add-on and harness Python load from this checkout (%s)" % ROOT,
        "machine": {"platform": platform.platform(), "cpu": _run(["sysctl", "-n", "machdep.cpu.brand_string"]),
                    "cores": os.cpu_count(), "memory_bytes": _run(["sysctl", "-n", "hw.memsize"])}}


def price_records(records, model, runtime, angelo_cache_ttl=""):
    """Per request: uncached input and the API-equivalent list price of its tokens (``model_profiles``).

    Each cache write is priced at the TTL the provider reported for it: Claude's usage splits every response's
    writes into 5-minute and 1-hour ones, and on a subscription Claude Code on the MCP side writes 1-hour
    entries as Angelo's chats do (``oneshot_metrics.cache_writes``). Writes without a reported TTL are priced at
    an assumed one, which ``list_price_basis`` names: for Angelo's Claude chats the session's TTL its receipt
    recorded (``cache_ttl``: 5 minutes on an API-key or team route, as Claude Code's default there), else
    ``--angelo-cache-ttl``, else 1 hour (records from before receipts carried it); 5 minutes for Claude Code;
    and on Codex, whose usage has no split, the 5-minute rate (a GPT model's only one). Unknown usage stays
    None, never zero. A record that names its own runtime and model (a reused MCP trial: ``gather``) is
    priced by them, not by this run's.
    """
    for record in records:
        usage = record.get("provider_usage")
        if not isinstance(usage, dict) or usage.get("input_tokens") is None:
            record.update(uncached_input_tokens=None, list_price_usd=None)
            continue
        own = record.get("runtime") or runtime
        angelo = own == "claude" and record.get("side") == "angelo"
        ttl = (record.get("cache_ttl") or angelo_cache_ttl or "1h") if angelo else "5m"
        unsplit = (oneshot_metrics.CODEX_UNSPLIT,) if own == "codex" else ()
        priced = record.get("model") or model
        record["uncached_input_tokens"] = max(0, usage["input_tokens"] - (usage.get("cached_input_tokens") or 0))
        record["list_price_usd"] = oneshot_metrics.list_cost(usage, priced, ttl)
        record["list_price_basis"] = oneshot_metrics.list_price_basis([usage], priced, ttl, *unsplit)
        if record.get("warm_ups"):  # Billed warm-ups recorded during this task (eval_receipts.warm_ups_since).
            extra = record.get("warm_up_usage")
            if not isinstance(extra, dict) or extra.get("input_tokens") is None:
                record.update(uncached_input_tokens=None, list_price_usd=None)  # Unmeasured, never omitted.
                continue
            record["uncached_input_tokens"] += max(0, extra["input_tokens"] - (extra.get("cached_input_tokens") or 0))
            cost = record["warm_up_list_price_usd"] = oneshot_metrics.list_cost(extra, priced, ttl)   # Cold start too.
            record["list_price_usd"] = (None if record["list_price_usd"] is None or cost is None
                                        else round(record["list_price_usd"] + cost, 4))
            record["list_price_basis"] = "%s, including %d warm-up(s)" % (
                oneshot_metrics.list_price_basis([usage, extra], priced, ttl, *unsplit), record["warm_ups"])


def finish(args, output, records, trial_meta, extra=None):
    """Results over valid trials; rate-limited trials stay in ``records``, counted and never graded."""
    price_records(records, args.model, getattr(args, "runtime", "claude"), getattr(args, "angelo_cache_ttl", ""))
    graded, invalid = limits.valid_records(records)
    summary = report.summarize(graded)
    summary["invalid_rate_limited"] = invalid
    reuse = Path(args.reuse_mcp_results).resolve() if getattr(args, "reuse_mcp_results", "") else None
    meta_plan, recorded = plan(args), integrity.recorded_plan(output) or {}
    for key in ("diagnostics", "cross_model_reuse"):   # A rebuilt summary keeps what the run was (eval_diagnostics).
        if recorded.get(key) and not meta_plan.get(key):
            meta_plan[key] = recorded[key]
    meta = {"finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "trials": args.trials, "plan": meta_plan,
            "method": METHOD + (METHOD_QUESTIONS if (plan(args)["gate_version"] or 0) >= 2 else [])
            + (METHOD_CODEX if getattr(args, "runtime", "claude") == "codex" else [])
            + (METHOD_OPENING if getattr(args, "angelo_opening_wait", 0.0) > 0 else [])
            + (METHOD_SCENARIO if scenario_specs.is_scenario(getattr(args, "task_set", "default")) else []),
            "threats": THREATS + (THREATS_OPENING if getattr(args, "angelo_opening_wait", 0.0) > 0 else [])
            + ([window_asymmetry(args)] if window_asymmetry(args) else []),
            "versions": versions(args, output, records, trial_meta),
            # Scripts, and the planned n of the recorded plan (a reused run's MCP trials too) against the valid n.
            "conditions": integrity.conditions(integrity.planned_plan(output, meta_plan, reuse), records),
            "trial_meta": trial_meta, **(extra or {})}
    meta["load_averages"] = [{"side": m.get("side"), "trial": m.get("trial"), "start": m.get("load_average_start"),
                              "end": m.get("load_average_end")} for m in trial_meta]
    results = {"meta": meta, "summary": summary, "observations": report.observations(graded),
               "tool_usage": report.tool_usage(graded), "session_curve": report.session_curve(graded),
               "records": records}
    (output / RESULTS).write_text(json.dumps(results, indent=2, default=str))
    (output / SUMMARY).write_text(report.markdown(results))
    if scenario_specs.is_oneshot(getattr(args, "task_set", "default")):
        import oneshot_report   # Its own verdict and report: the stills, the pixel checks and the path metrics.
        oneshot_report.finish(output, args.task_set, results, meta, args.stock_blender)
    elif scenario_specs.is_scenario(getattr(args, "task_set", "default")):
        import scenario_report
        import scenario_scorecard
        card = scenario_scorecard.collect(output, args.task_set, results, args.stock_blender)
        scenario_report.write(output, card, meta)
    # A diagnostic run's reports, scorecards and pages say so (and list its ask-why replies), when rebuilt too.
    diagnostics.mark_outputs(output, meta_plan.get("diagnostics"))
    return results


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    import oneshot_group
    if oneshot_group.group_of(argv):   # A set of one-shot tests: this same command once per member, then one table.
        return oneshot_group.run(argv, main)
    args = parse(argv)
    if args.summarize_only:   # An earlier run is summarized as it was asked, whatever today's gate-set version is.
        args.gate_version = gate.recorded_version(Path(args.summarize_only).resolve())
    print("PLANNED", json.dumps(plan(args)), flush=True)
    # Never silent: a run whose sides get different window states says so before it starts and in its report.
    asymmetry = window_asymmetry(args)
    if asymmetry:
        print("WINDOW STATE:", asymmetry, file=sys.stderr, flush=True)
    if args.plan_only:
        return 0
    if not args.summarize_only and args.sides != "mcp" and args.angelo_binary and Path(args.angelo_binary).is_file():
        if blocked := addon_pin.problem(Path(args.angelo_binary).resolve(), args.allow_addon_mismatch):
            print("REFUSED:", blocked, file=sys.stderr)   # The Angelo side would import another add-on.
            return 2
    if args.summarize_only:
        output = Path(args.summarize_only).resolve()
        args.connection_id = args.connection_id or ""
        if not args.claude_config_dir and args.connection_id:
            profiles = Path(args.source_connections_dir or ".") / "claude-profile"
            args.claude_config_dir = str(profiles / args.connection_id)
        reuse = Path(args.reuse_mcp_results).resolve() if args.reuse_mcp_results else None
        recorded = integrity.recorded_plan(output)
        problems = integrity.summary_problems(output, plan(args), recorded, tasks(args), reuse,
                                              getattr(args, "cross_model_reuse", False))
        if problems:   # Every reuse check, against the trials as recorded, and nothing re-planned.
            print("REFUSED:", "; ".join(problems), file=sys.stderr)
            return 2
        report.use_tasks(tasks(args))
        trials = integrity.trial_count(output, recorded or plan(args), reuse)   # No trial folder is dropped.
        records, trial_meta = gather(output, trials, reuse, tasks(args), getattr(args, "returning_user", False))
        finish(args, output, records, trial_meta, {"rebuilt_from_raw": True})
        print("SUMMARY", output / SUMMARY, flush=True)
        return 0
    if args.dry_run_scenario:
        import scenario_dry_run

        def summarize(output, mcp_trials):
            """A billed run's own summary path, over the dry run's raw trial folders."""
            for trial in range(1, mcp_trials + 1):
                mark_mcp_streams(args, output, trial)
                (output / ("mcp-trial-%02d-source.json" % trial)).write_text(json.dumps(addon_pin.stamp(   # As a run's.
                    Path(args.angelo_binary).resolve() if args.sides != "mcp" and args.angelo_binary else None)))
            report.use_tasks(tasks(args))
            records, trial_meta = gather(output, mcp_trials, None, tasks(args), getattr(args, "returning_user", False))
            return finish(args, output, records, trial_meta, {"dry_run_scenario": "scripted models; unbilled"})
        outcome = scenario_dry_run.run(args.task_set, args.output, angelo_binary=args.angelo_binary,
                                       stock_blender=args.stock_blender, mcp_server_dir=args.mcp_server_dir,
                                       mcp_addon_dir=args.mcp_addon_dir, claude=args.claude, codex=args.codex,
                                       task_timeout=args.task_timeout, demo_record=args.demo_record,
                                       summarize=summarize, sides=DRY_RUN_SIDES[args.sides],
                                       diagnostics=diagnostics.stamp(args), warm_up=args.warm_up_only,
                                       runtime=getattr(args, "runtime", "claude"))
        print("SCENARIO_DRY_RUN", json.dumps({k: outcome[k] for k in ("success", "checks", "elapsed_s")}), flush=True)
        return 0 if outcome["success"] else 1
    problems, output = preflight(args)
    if problems:
        print("REFUSED:", "; ".join(problems), file=sys.stderr)
        return 2
    if args.preflight_only:   # Every check a billed start makes (paths, sign-ins, the folder); nothing is sent.
        print("PREFLIGHT_OK", json.dumps({"output": str(output)}), flush=True)
        return 0
    if scenario_specs.is_scenario(args.task_set):
        # Unbilled and before any billed message: both sides then read the same cached references.
        import scenario_references
        scenario_references.make(scenario_specs.scenario_id(args.task_set), args.stock_blender)
    output.mkdir(parents=True, exist_ok=True)
    if args.dry_run_mcp:
        side = codex_mcp if getattr(args, "runtime", "claude") == "codex" else mcp
        outcome = side.dry_run(mcp_config(args, output), output / "mcp-dry-run")
        print("DRY_RUN", json.dumps({k: outcome.get(k) for k in ("success", "checks", "objects_at_start")}), flush=True)
        return 0 if outcome["success"] else 1
    if args.warm_up_only:   # Never a trial: under warm-up/, outside the run's plan and trial count (eval_warm_up).
        import bench_warm_up
        return bench_warm_up.run(args, output, mcp_config(args, output), angelo_command, lambda records: price_records(
            records, args.model, getattr(args, "runtime", "claude"), getattr(args, "angelo_cache_ttl", "")))
    config = mcp_config(args, output)
    if not (output / RUN_PLAN).is_file():
        (output / RUN_PLAN).write_text(json.dumps(plan(args), indent=2))
    stopped, rate_limited, started = set(), None, set()
    order = [(s, t) for t in range(1, args.trials + 1) for s in ("mcp", "angelo") if args.sides in ("both", s)]
    for side, trial in order:
        if side in stopped:
            continue
        if trial_done(output, side, trial):
            continue  # --resume: a completed trial is kept as it is.
        if args.max_new_trials and trial not in started and len(started) >= args.max_new_trials:
            break
        started.add(trial)
        set_aside(output, side, trial)
        if side == "mcp":
            runner = codex_mcp.run_trial if config["runtime"] == "codex" else mcp.run_trial
            (output / ("mcp-trial-%02d-source.json" % trial)).write_text(json.dumps(addon_pin.stamp(
                Path(args.angelo_binary).resolve() if args.sides != "mcp" else None)))
            outcome = runner(config, trial, output / ("mcp-trial-%02d" % trial))
            mark_mcp_streams(args, output, trial)
            failed = outcome.get("aborted") or outcome.get("error") or not outcome["tasks"]
            limited = outcome.get("invalid") == limits.INVALID and outcome.get("aborted")
        else:
            outcome = run_angelo_trial(args, output, trial)
            (output / ("angelo-trial-%02d-launch.json" % trial)).write_text(json.dumps(outcome, indent=2))
            harness = output / ("angelo-trial-%02d" % trial) / "eval-live-agent.json"
            trial_report = (json.loads(harness.read_text()).get("trials") or [{}])[0] if harness.is_file() else {}
            failed = trial_report.get("aborted") or not trial_report.get("tasks")
            limited = trial_report.get("invalid") == limits.INVALID and trial_report.get("aborted")
        print("TRIAL", side, trial, json.dumps({"failed": bool(failed), "invalid": "rate_limited" if limited else None}),
              flush=True)
        if limited:
            # Both sides share the account: stop the whole run, not one side.
            rate_limited = "%s trial %d: %s" % (side, trial, limited)
            print(limited, file=sys.stderr, flush=True)
            stopped.update(("mcp", "angelo"))
            break
        if failed:
            stopped.add(side)  # Broken startup or sign-in: send no further billed messages on that side.
    reuse = Path(args.reuse_mcp_results).resolve() if args.reuse_mcp_results else None
    report.use_tasks(tasks(args))
    records, trial_meta = gather(output, args.trials, reuse, tasks(args), getattr(args, "returning_user", False))
    results = finish(args, output, records, trial_meta, {"stopped_sides": sorted(stopped), "rate_limited": rate_limited})
    print("BENCH_DONE", json.dumps({"output": str(output), "gate_versions": results["summary"].get("gate_versions"),
                                    "totals": results["summary"]["totals"],
                                    "invalid_rate_limited": results["summary"]["invalid_rate_limited"]}), flush=True)
    if rate_limited or results["summary"]["invalid_rate_limited"]:
        return limits.EXIT_RATE_LIMITED
    return 0 if not stopped else 1


if __name__ == "__main__":
    sys.exit(main())
