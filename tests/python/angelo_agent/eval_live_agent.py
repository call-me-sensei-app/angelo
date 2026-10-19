# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Opt-in, BILLED live-agent latency evaluation of the real Angelo controller.

Every planned task sends one real message to the selected provider connection
and is billed to that account. The script always prints the plan first, then
refuses to start unless BOTH ``--authorize-billed-runs`` and an explicit
``--connection-id`` are given. ``--plan-only`` prints the plan and exits.

Usage, from the repository root (host Python)::

    python3 tests/python/angelo_agent/eval_live_agent.py --plan-only
    python3 tests/python/angelo_agent/eval_live_agent.py --authorize-billed-runs \\
        --connection-id UUID [--trials 3] [--model ID] [--effort low] [--output DIR]

Isolation: each trial is a new disposable ``--factory-startup`` GUI process with its own Blender user folders
(config, scripts, datafiles, extensions), Angelo state (so its first message starts the runtime cold) and QA
directory, and ``ANGELO_DISABLE_DEFAULT_CHAT=1`` (the documented automation opt-out, so no chat editor is drawn).
Its connection store is a new private directory holding only the selected connection's metadata, copied from
``--source-connections-dir`` (default: the app's connection directory). No credential is read or copied:
subscription runtimes keep using their existing native sign-in in place. API-key connections are refused: their
keys are never copied into the isolated profile, and ``ANGELO_DISABLE_KEYCHAIN=1`` keeps every trial from reading,
writing or waiting on the OS key store. No user project is opened; the scene is the factory one.
``--input-isolation on`` (the default) adds ``--enable-event-simulate``: real keyboard and mouse input never
reaches a trial, because keystrokes that once landed in two trial windows started animation playback and
failed a request. ``--after-failure reset`` grades every task even after an earlier one failed
(``live.reset_for_task``).

Approvals are the product default (``eval_approval``); of what still asks, the
driver approves only typed bounded scene tools (``HARNESS_APPROVABLE``). No Fast.

Results (``eval-live-agent.json`` in the output directory) come from each
request's local performance receipt (``telemetry.py``, ``provider_usage.py``)
plus closed host checks; p50/p75 per journey are computed over completed,
host-verified tasks only. ``grading`` states passed, failed, ungraded and
skipped over every valid run: a run is passed only when a check read its
result (``eval_grading``). Claude typed-plan rounds and Codex turns are different
units and are never compared; Codex internal rounds are not observable. Timing
on a machine running other work is measured under that load; say so.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for _path in (str(ROOT / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import bench_addon_pin as addon_pin  # noqa: E402  (the add-on the trials import must be the app's)
import bench_workdirs as workdirs  # noqa: E402
import eval_answers  # noqa: E402
import eval_approval as approval  # noqa: E402
import eval_diagnostics as diagnostics  # noqa: E402  (--debug-thinking, --ask-why-on-miss)
import eval_warm_up as warm_up  # noqa: E402  (--warm-up-only)
import eval_grading  # noqa: E402
import eval_live_tasks as live  # noqa: E402
import eval_provider_limits as limits  # noqa: E402
import eval_returning_user as returning  # noqa: E402
import gui_launch  # noqa: E402
from angelo_agent import product_env  # noqa: E402  (standard library only)
import scenario_specs  # noqa: E402

SESSION_SCRIPT = HERE / "eval_live_agent_session.py"
# Subscriptions first: both runtimes' sign-ins.
SUPPORTED_MODES = frozenset({"codex_subscription", "claude_subscription"})
# Pay-per-token keys only with --allow-api-key-connection, in place (see isolated_connection).
API_KEY_MODES = frozenset({"claude_api", "codex_api", "grok_api", "minimax_api", "kimi_api"})
EFFORTS = ("none", "minimal", "low", "medium", "high")


class Refusal(ValueError):
    """A reason the run must not start; nothing billed has happened."""


def parse(argv):
    parser = argparse.ArgumentParser(description="BILLED live-agent latency evaluation (see module docstring)")
    parser.add_argument("--authorize-billed-runs", action="store_true",
                        help="required: confirm every planned message is billed to the selected connection")
    parser.add_argument("--connection-id", default="", help="required: UUID of ONE existing coding connection")
    parser.add_argument("--plan-only", action="store_true", help="print the plan and exit without running")
    parser.add_argument("--preflight-only", action="store_true",
                        help="run every check of a billed start (binary, --output, connection copy) and stop; unbilled")
    parser.add_argument("--trials", type=int, default=3, help="fresh processes (cold starts); 1-20")
    parser.add_argument("--model", default="", help="model ID; default is the connection's default")
    parser.add_argument("--effort", choices=EFFORTS, default="low")
    parser.add_argument("--task-timeout", type=float, default=None,
                        help="seconds per task before Stop (default 300; a one-shot test's own time cap)")
    parser.add_argument("--cost-cap", type=float, default=0.,
                        help="one-shot task sets only: stop the turn once its model usage passes this many USD at "
                             "list prices (default: the test's own cap)")
    parser.add_argument("--blender", default=product_env.get("BINARY") or "",
                        help="compiled Angelo binary (default: $ANGELO_BINARY)")
    parser.add_argument("--output", default="", help="new results directory (default: a new temporary one)")
    parser.add_argument("--source-connections-dir", default="",
                        help="where the selected connection's metadata is read (default: the app's directory)")
    parser.add_argument("--task-set", choices=live.TASK_SETS, default="default",
                        help="default: the six benchmark tasks; coverage: five native-coverage edits; long: one long chat of "
                             "--long-tasks tasks; survey: 65 realistic requests for survey_report.py; "
                             "survey-questions: its six questions and the cube they ask about (each answer form once)")
    parser.add_argument("--long-tasks", type=int, default=live.LONG_SESSION_TASKS, help="tasks in the long set; 2-120")
    parser.add_argument("--observation", choices=("legacy", "turn-context"), default="legacy",
                        help="legacy: angelo_inspect_scene's first 32 objects (earlier runs); turn-context: the chat "
                             "editor's own turn_context.prepare, with its change feed")
    parser.add_argument("--context-rebase-tokens", type=int, default=0,
                        help="the chat's context rebase threshold (0, the product default, is off)")
    parser.add_argument("--cache-ttl", choices=("", "5m", "1h"), default="",
                        help="prompt-cache TTL of the chat's new Claude sessions; default: the product's choice "
                             "(1h for an interactive chat)")
    parser.add_argument("--approve-delegation", default="", metavar="MODEL",
                        help="approve read-only consultations of exactly this catalog model on the run's own "
                             "connection (each one is billed); without it every consultation is declined")
    parser.add_argument("--allow-api-key-connection", action="store_true",
                        help="also accept a pay-per-token API-key connection. It must be the only connection in a "
                             "dedicated --source-connections-dir prepared in the app (its key stays in that profile's "
                             "OS key-store item and is never copied); every token is billed to that account")
    parser.add_argument("--stable-state-dir", default="",
                        help="run every trial's fresh Angelo state at this one path (moved into the trial folder "
                             "afterwards), so the Claude working directory is the same across trials, as for one user")
    gui_launch.add_arguments(parser)   # --input-isolation and --window-state
    parser.add_argument("--after-failure", choices=live.AFTER_FAILURE_MODES, default="skip",
                        help="skip (default, as earlier runs): tasks needing a missing object are skipped; reset: the "
                             "harness restores the reference state each task needs and runs it, so every task is graded")
    parser.add_argument("--team-mode", choices=("",) + live.TEAM_MODES, default="",
                        help="run through the Angelo gateway with the team connection of this mode instead of "
                             "--connection-id. --source-connections-dir must be a dedicated profile where someone "
                             "signed in to the team in the app (its sign-in stays in that profile's OS key-store "
                             "item, used in place); every token is billed to the organization's own provider key")
    scenario_specs.add_live_arguments(parser)   # --python-approvals, --demo-record, --stage-copies, --render-device
    parser.add_argument("--trusted-scripts", action="store_true",
                        help="turn on the chat's Trusted scripts (scene Python runs without a review, as an artist "
                             "who switched it on would; head-to-heads match the MCP side's no-prompt setup)")
    parser.add_argument("--codex-home", default="",
                        help="Codex connections: run with this dedicated, signed-in profile "
                             "(.../codex-profile/<connection id>, signed in with CODEX_HOME=<it> codex login) "
                             "instead of the connection's own; never ~/.codex")
    for options in (approval, returning, diagnostics, warm_up):  # Approval, returning user, diagnostics, warm-up.
        options.add_arguments(parser)
    addon_pin.add_argument(parser)   # --allow-addon-mismatch
    args = warm_up.check(parser, diagnostics.check(parser, approval.check(parser, returning.check(parser, parser.parse_args(argv)))))
    if args.team_mode:
        if args.connection_id or args.codex_home or args.allow_api_key_connection:
            parser.error("--team-mode replaces --connection-id, --codex-home and --allow-api-key-connection")
        args.connection_id = live.TEAM_PREFIX + args.team_mode
    oneshot = scenario_specs.is_oneshot(args.task_set)
    args.task_timeout = args.task_timeout or scenario_specs.wall_cap(args.task_set) or 300.
    longest = scenario_specs.ONESHOT_MAX_WALL_S if oneshot else 1800   # One turn carries a one-shot test's whole build.
    if not 30 <= args.task_timeout <= longest:
        parser.error("--task-timeout must be 30-%d seconds" % longest)
    if args.cost_cap and not oneshot:
        parser.error("--cost-cap needs a one-shot task set")
    if not 1 <= args.trials <= 20:
        parser.error("--trials must be 1-20")
    if not 2 <= args.long_tasks <= 120:
        parser.error("--long-tasks must be 2-120")
    if scenario_specs.is_scenario(args.task_set) and args.after_failure != "skip":
        parser.error("scenario sets build on every earlier turn's real result: use --after-failure skip")
    if (args.demo_record or args.stage_copies or args.render_device) and not scenario_specs.is_scenario(args.task_set):
        parser.error("--demo-record, --stage-copies and --render-device need a scenario task set")
    if not 0 <= args.context_rebase_tokens <= 1000000:
        parser.error("--context-rebase-tokens must be 0-1000000")
    if args.approve_delegation and (len(args.approve_delegation) > 128
                                    or not all(32 < ord(c) < 127 for c in args.approve_delegation)):
        parser.error("--approve-delegation takes one exact model ID")
    return args


def benchmark_codex_profile(codex_home, connection_id, mode):
    """A validated dedicated Codex profile for one connection (never ``~/.codex``), as a string.

    Only the sign-in file's existence is checked; it is never read.
    """
    from angelo_agent.storage import valid_profile_directory
    if not mode.startswith("codex_"):
        raise Refusal("--codex-home is only for Codex connections")
    path = Path(codex_home).expanduser().resolve()
    if path == (Path.home() / ".codex").resolve():
        raise Refusal("--codex-home must be a dedicated profile, never ~/.codex")
    try:
        valid_profile_directory(str(path), connection_id, mode)
    except ValueError:
        raise Refusal("--codex-home must be named .../codex-profile/%s" % connection_id) from None
    if not (path / "auth.json").is_file():
        raise Refusal("--codex-home is not signed in (run: CODEX_HOME=%s codex login)" % path)
    return str(path)


def team_connection(source_directory, mode):
    """The signed-in team's connection of ``mode``, used in place in a profile dedicated to it.

    The trial restores the team sign-in from that profile's OS key-store item
    (as at a normal launch), so nothing is copied; the profile must hold no
    stored connection, so the trial can only use the team's.
    """
    from angelo_agent.connections import ConnectionStore
    source = Path(source_directory)
    if not source.is_dir():
        raise Refusal("No team profile at %s" % source)
    if ConnectionStore(source).records:
        raise Refusal("A team run uses its profile in place: --source-connections-dir must hold no stored connection. "
                      "Prepare a dedicated profile (ANGELO_CONNECTIONS_DIR=<it>) and sign in to the team there.")
    return {"id": live.TEAM_PREFIX + mode, "name": "Angelo team", "mode": mode, "reuse_native": False,
            "in_place": str(source.resolve())}


def isolated_connection(source_directory, connection_id, destination, *, allow_api_key=False, codex_home=""):
    """Copy ONE connection's metadata (never credentials) into a new private store.

    A profile-based subscription keeps pointing at its existing native profile
    directory, exactly as ``ConnectionStore.import_metadata`` does. An API-key
    connection (only with ``allow_api_key``) is used in place instead: its key
    lives in the OS key-store item of its own connections directory, which a copy
    could not read, so that directory must be dedicated to it (no other
    connection) and the app reads its own key there as in normal use.
    """
    from angelo_agent.connections import ConnectionStore
    from angelo_agent.provider_registry import CODING
    from angelo_agent.storage import native_profile_directory, valid_profile_directory
    source = Path(source_directory)
    if not source.is_dir() or not (source / "connections.json").is_file():
        raise Refusal("No connection store at %s" % source)
    record = ConnectionStore(source).records.get(connection_id)
    if record is None:
        raise Refusal("Connection %s does not exist in %s" % (connection_id, source))
    from angelo_agent.provider_registry import plan_terms
    if record["group"] == CODING and plan_terms(record["mode"]):
        raise Refusal("%s is a flat-rate coding-plan key, for its owner's personal use; automated benchmarks use the "
                      "provider's pay-per-token API connection instead" % record["mode"])
    if record["group"] == CODING and record["mode"] in API_KEY_MODES:
        if not allow_api_key:
            raise Refusal("%s is a pay-per-token API-key connection; pass --allow-api-key-connection to bill it, "
                          "or use a Codex or Claude subscription connection" % record["mode"])
        others = sorted(set(ConnectionStore(source).records) - {connection_id})
        if others:
            raise Refusal("An API-key connection runs in place: --source-connections-dir must hold only that "
                          "connection (it holds %d others). Prepare a dedicated directory in the app." % len(others))
        return {"id": connection_id, "name": record["name"], "mode": record["mode"], "reuse_native": False,
                "in_place": str(source.resolve())}
    if record["group"] != CODING or record["mode"] not in SUPPORTED_MODES:
        raise Refusal("Use a Codex or Claude subscription coding connection; Generative AI connections cannot "
                      "run the agent (keys are never copied into the isolated profile)")
    record = dict(record)
    if codex_home:
        record.update(reuse_native=False, native_profile=benchmark_codex_profile(codex_home, connection_id, record["mode"]))
    if not record["reuse_native"] and not record.get("native_profile"):
        family = "claude" if record["mode"].startswith("claude_") else "codex"
        # The default store may still use a profile retained in the pre-rename data folder.
        record["native_profile"] = valid_profile_directory(
            str(native_profile_directory(source.absolute(), family + "-profile", connection_id)),
            connection_id, record["mode"])
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise Refusal("The isolated connection directory must be new and empty")
    from angelo_agent.connections import MODES
    store = ConnectionStore(destination)
    store.records, store.active = {connection_id: record}, connection_id
    # Startup discovery would otherwise add a connection reusing the developer's
    # everyday native sign-in (for example their ChatGPT/Codex login) next to the
    # selected one. Each trial must run with exactly the selected connection.
    store.ignored_native_modes = set(MODES.values())
    store.save()
    return {"id": connection_id, "name": record["name"], "mode": record["mode"], "reuse_native": record["reuse_native"]}


# Blender's user folders a trial keeps inside its own folder (BLENDER_USER_CONFIG is the trial's ``config``).
USER_FOLDERS = ("RESOURCES", "SCRIPTS", "DATAFILES", "EXTENSIONS")


def trial_environment(folder, accounts, args, trial, state=None, *, keychain=False):
    """``keychain``: an in-place API-key connection may read its own saved key."""
    from angelo_agent import product_env
    environment = dict(os.environ)
    for name in ("CONNECTIONS_DIR", "DISABLE_DEFAULT_CHAT", "DISABLE_KEYCHAIN"):
        product_env.clear(name, environment)  # Only the values set below; no deprecated spelling.
    environment.update(
        ANGELO_EVAL_TASK_SET=getattr(args, "task_set", "default"),
        ANGELO_EVAL_LONG_TASKS=str(getattr(args, "long_tasks", live.LONG_SESSION_TASKS)),
        ANGELO_EVAL_OBSERVATION=getattr(args, "observation", "legacy"),
        ANGELO_EVAL_CONTEXT_REBASE_TOKENS=str(getattr(args, "context_rebase_tokens", 0)),
        ANGELO_EVAL_AFTER_FAILURE=getattr(args, "after_failure", "skip"),
        ANGELO_EVAL_CACHE_TTL=getattr(args, "cache_ttl", ""),
        ANGELO_EVAL_APPROVE_DELEGATION=getattr(args, "approve_delegation", ""),
        ANGELO_EVAL_TRUSTED_SCRIPTS="1" if getattr(args, "trusted_scripts", False) else "",
        BLENDER_USER_CONFIG=str(folder / "config"), ANGELO_STATE_DIR=str(state or folder / "state"),
        ANGELO_CONNECTIONS_DIR=str(accounts), ANGELO_QA_DIR=str(folder / "qa"), ANGELO_DISABLE_DEFAULT_CHAT="1",
        ANGELO_EVAL_AUTHORIZED="1", ANGELO_EVAL_CONNECTION_ID=args.connection_id, ANGELO_EVAL_TRIAL=str(trial),
        ANGELO_EVAL_TEAM_MODE=getattr(args, "team_mode", ""),
        ANGELO_EVAL_TASK_TIMEOUT=str(args.task_timeout), ANGELO_EVAL_EFFORT=args.effort,
        ANGELO_EVAL_MODEL=args.model,
        # Measurement runs keep the selected model: no automatic switch after a refusal.
        CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK="1", **approval.environment(args), **diagnostics.environment(args),
        **warm_up.environment(args))
    if not keychain:
        environment["ANGELO_DISABLE_KEYCHAIN"] = "1"
    if scenario_specs.is_scenario(getattr(args, "task_set", "default")):
        environment.update({scenario_specs.REFERENCES_ENV: str(scenario_specs.reference_root()),
                            "ANGELO_EVAL_PYTHON_APPROVALS": getattr(args, "python_approvals", "approve"),
                            "ANGELO_EVAL_DEMO_RECORD": "1" if getattr(args, "demo_record", False) else "",
                            "ANGELO_EVAL_STAGE_COPIES": "1" if getattr(args, "stage_copies", False) else "",
                            "ANGELO_EVAL_RENDER_DEVICE": getattr(args, "render_device", "")})
        if getattr(args, "cost_cap", 0):   # One-shot task sets only (oneshot_session.COST_CAP_ENV).
            environment["ANGELO_EVAL_COST_CAP"] = "%g" % args.cost_cap
    # Every Blender user folder stays inside the trial (``run_trial`` creates them): scenario sessions save files
    # and run Undo, and the app's default user folder is the person's own Angelo folder (doc/angelo/app_identity.rst,
    # "Folders"). HOME stays as the harness has it: subscription runtimes find their Keychain sign-in through it.
    for name in USER_FOLDERS:
        environment["BLENDER_USER_" + name] = str(folder / "blender-user" / name.lower())
    return returning.environment(environment, args)   # --profile-dir: the profile's state and user folders.


def demo_geometry():
    import scenario_demo
    return scenario_demo.WINDOW_GEOMETRY


def job_wait(args):
    """Seconds a scenario set may wait for its background job (0 for every other task set)."""
    task_set = getattr(args, "task_set", "default")
    return scenario_specs.job_wait(scenario_specs.scenario_id(task_set)) if scenario_specs.is_scenario(task_set) else 0


def stable_state(args):
    """The shared state path of --stable-state-dir, which must be new or empty before each trial."""
    if not getattr(args, "stable_state_dir", ""):
        return None
    path = Path(args.stable_state_dir).resolve()
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise Refusal("--stable-state-dir must be a new or empty directory: %s" % path)
    return path


def run_trial(binary, output, accounts, args, trial, task_count, *, keychain=False):
    folder = output / ("trial-%02d" % trial)
    for name in ("config", "state", "qa"):
        (folder / name).mkdir(parents=True)
    for name in USER_FOLDERS:
        (folder / "blender-user" / name.lower()).mkdir(parents=True)
    if "aborted" in (pinned := addon_pin.trial_stamp(binary, getattr(args, "allow_addon_mismatch", False))):
        return pinned   # The checkout changed after the run began: no trial on another add-on.
    state = workdirs.trial_state(folder, stable_state(args))   # Outside a checkout: no project context (bench_workdirs).
    flags = gui_launch.window_flags(getattr(args, "window_state", "behind"), getattr(args, "input_isolation", "on") == "on")
    command = [str(binary), "--factory-startup", "--disable-autoexec", "--python-exit-code", "1", *flags,
               "--window-geometry", *(demo_geometry() if getattr(args, "demo_record", False) else
                                      ("60", "60", "1400", "900")), "--python", str(SESSION_SCRIPT)]
    # Each task may run to its timeout, then a stop grace and a preview accept; a scenario with a
    # background job (a render) may also wait for it once.
    timeout = task_count * (2 * args.task_timeout + 60) + 120 + job_wait(args) + diagnostics.extra_seconds(args)
    started = time.monotonic()
    with (folder / "run.log").open("w") as log:
        try:
            code = subprocess.run(command, cwd=ROOT, env=trial_environment(folder, accounts, args, trial, state,
                                                                           keychain=keychain),
                                  stdout=log, stderr=subprocess.STDOUT, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            code = "timeout"
    if state is not None:
        # Keep the trial's state with its results and leave the shared path empty.
        (folder / "state").rmdir()
        shutil.move(str(state), str(folder / "state"))
    try:
        report = json.loads((folder / "qa" / "trial.json").read_text())
    except (OSError, ValueError) as error:
        report = {"tasks": [], "success": False, "error": "No trial report: %s" % error}
    report.update(exit_code=code, elapsed_s=round(time.monotonic() - started, 3), log=str(folder / "run.log"),
                  addon=pinned)
    return report


def aggregate(plan, connection, trials):
    """Outcomes, grades and journeys over valid trials; rate-limited ones are counted, never graded.

    ``grading`` states passed, failed, ungraded and skipped over every valid
    run (``eval_grading``): only a run whose result a check read is passed.
    """
    every = [task for trial in trials for task in trial.get("tasks", [])]
    valid = [trial for trial in trials if trial.get("invalid") != limits.INVALID]
    records = [task for trial in valid for task in trial.get("tasks", [])]
    planned = len(valid) * plan["tasks_per_trial"] if "tasks_per_trial" in plan else None
    # What the run was graded as: the planned set's stamp (contract, digest, gate-set or survey version).
    graded_as = {key: value for key, value in (plan.get("grading") or {}).items() if key not in ("contract", "tasks")}
    contracts = {(trial.get("grading") or {}).get("contract") or 0 for trial in valid
                 if trial.get("grading") or trial.get("tasks")}
    if not contracts:
        contracts = {(plan.get("grading") or {}).get("contract") or 0}
    contract = next(iter(contracts)) if len(contracts) == 1 else None
    if len(contracts) > 1:
        graded_as["contracts"] = sorted(contracts)
    load = list(os.getloadavg()) if hasattr(os, "getloadavg") else None
    return {"plan": plan, "connection": connection, "load_average_at_end": load,
            "messages_sent": sum(task.get("status") != "skipped" and task.get("receipt") is not None for task in every),
            "invalid_trials": limits.invalid_trials(trials),
            "grading": dict(eval_grading.totals(records, planned=planned), contract=contract,
                            answers=eval_answers.lenient_counts(records), **graded_as),
            "journeys": live.summarize_journeys(records), "tasks": live.task_outcomes(records), "trials": trials,
            "method": "real controller in disposable factory-startup GUI processes; per-request local receipts; "
                      "closed host checks; a run is passed only when a check read its result, an unchecked answer "
                      "or edit is ungraded; p50/p75 over completed host-verified tasks only",
            "caveats": ["Wall-clock latency includes provider network and inference; on a machine running other "
                        "work it is measured under that load.",
                        "Approval waits are the harness's own automatic approvals, not a human's.",
                        "Claude typed-plan rounds and Codex turns are different units; Codex internal rounds "
                        "are not observable."]}


def main(argv=None):
    args = parse(argv)
    tasks = live.tasks_for(args.task_set, args.long_tasks)
    plan = dict(live.plan(args.trials, tasks), input_isolation=args.input_isolation, after_failure=args.after_failure,
                window_state=gui_launch.side_window_state("angelo", args.window_state), refusal_fallback="disabled",
                cache_ttl=args.cache_ttl or "product default", **diagnostics.plan(args),
                approve_delegation=args.approve_delegation or None, grading=eval_grading.stamp(args.task_set, tasks),
                trusted_scripts=bool(args.trusted_scripts), **approval.plan(args), **returning.plan(args),
                **warm_up.plan(args))
    if scenario_specs.is_scenario(args.task_set):
        plan.update(scenario=scenario_specs.plan(scenario_specs.scenario_id(args.task_set)),
                    python_approvals=args.python_approvals, demo_record=args.demo_record,
                    stage_copies=args.stage_copies, render_device=args.render_device or "factory preference")
    if args.approve_delegation:
        plan["billing"] += (" Each approved consultation of %s is a further billed session on the same "
                            "connection." % args.approve_delegation)
    print("PLANNED", json.dumps(plan), flush=True)
    if args.plan_only:
        return 0
    try:
        # --preflight-only sends nothing, so it checks as an authorized run would.
        reasons = live.refusal_reasons(args.authorize_billed_runs or args.preflight_only, args.connection_id)
        if reasons:
            raise Refusal("; ".join(reasons))
        if not args.blender:
            raise Refusal("Pass --blender or set ANGELO_BINARY to the compiled Angelo executable")
        binary = Path(args.blender)
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise Refusal("No executable Angelo binary at %s" % binary)
        if blocked := addon_pin.problem(binary, args.allow_addon_mismatch):   # The trials import this checkout's.
            raise Refusal(blocked)
        stable_state(args)  # Refuse a non-empty shared state path before anything is billed.
        if scenario_specs.is_scenario(args.task_set):
            name = scenario_specs.scenario_id(args.task_set)
            if scenario_specs.manifest(name) is None:
                raise Refusal("No references for scenario %s; run scenario_references.py --scenario %s first"
                              % (name, name))
        output = Path(args.output or tempfile.mkdtemp(prefix="angelo-live-eval-")).resolve()
        output.mkdir(parents=True, exist_ok=True)
        if any(output.iterdir()):
            raise Refusal("Use a new, empty --output directory")
        if not args.source_connections_dir:
            from angelo_agent.storage import connection_directory
            args.source_connections_dir = str(connection_directory())
        connection = (team_connection(args.source_connections_dir, args.team_mode) if args.team_mode else
                      returning.kept_connection(args, args.connection_id) or
                      isolated_connection(args.source_connections_dir, args.connection_id,
                                          returning.accounts(args, output), allow_api_key=args.allow_api_key_connection,
                                          codex_home=args.codex_home))
    except Refusal as refusal:
        print("REFUSED:", refusal, file=sys.stderr)
        return 2
    print("CONNECTION", json.dumps(connection), flush=True)
    if args.preflight_only:   # Every check of a billed start passed (binary, folder, connection copy); nothing sent.
        print("PREFLIGHT_OK", json.dumps({"output": str(output)}), flush=True)
        return 0
    trials = []
    for trial in range(1, args.trials + 1):
        accounts = Path(connection["in_place"]) if connection.get("in_place") else returning.accounts(args, output)
        trials.append(run_trial(binary, output, accounts, args, trial, len(tasks),
                                keychain=bool(connection.get("in_place"))))
        print("TRIAL", trial, json.dumps({key: trials[-1].get(key) for key in ("success", "exit_code", "aborted")}),
              flush=True)
        if trials[-1].get("invalid") == limits.INVALID:
            print(trials[-1]["aborted"], file=sys.stderr, flush=True)
            break  # The account's limit: every further message would fail the same way.
        if trials[-1].get("aborted") or not trials[-1].get("tasks"):
            break  # Broken sign-in or startup: send no further billed messages.
    report = aggregate(plan, connection, trials)
    (output / "eval-live-agent.json").write_text(json.dumps(report, indent=2, default=str))
    print("LIVE_EVAL_DONE", json.dumps({"output": str(output), "messages_sent": report["messages_sent"],
          "grading": report["grading"],
          "journeys": {name: {k: v for k, v in value.items() if k in ("samples", "p50_ms", "p75_ms")}
                       for name, value in report["journeys"].items()}}), flush=True)
    if report["invalid_trials"]:
        print("INVALID_TRIALS", json.dumps(report["invalid_trials"]), "(excluded from every pass rate)", flush=True)
        return limits.EXIT_RATE_LIMITED
    return 0 if all(trial.get("success") for trial in trials) and len(trials) == args.trials else 1


if __name__ == "__main__":
    sys.exit(main())
