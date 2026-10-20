# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The release measurement campaign: release gate and head-to-head, checkpointed, on a quiet machine.

The steps, their order and estimates are in ``final_runs_plan``; the quiet-machine
checks in ``final_runs_load``. Every billed message goes through the existing
harnesses (``bench_mcp_vs_angelo.py``, ``eval_live_agent.py`` and the pilot's
``run_pilot.py``), one trial at a time, so the campaign can stop anywhere and
resume: a finished trial is never run again, a trial cut short is kept and run
again. Before every trial the machine must be quiet (load average at or below
the threshold, default 6, and no other heavy process); otherwise the run pauses
until it is and writes when and why. While a trial runs, the load is sampled
every 5 s and the trial's record says whether it was contended. A provider
rate or usage limit (harness exit code 3) blocks that subscription for a
cooldown while the other account's steps continue. A trial another program
contended (``final_runs_load``) is set aside under ``contended/`` and run again,
once by default; ``--stop-after-hours`` starts no trial after that long, and a
``STOP`` file in the campaign folder ends the run after the current trial.
Usage::

    python3 final_runs.py freeze --source .../bin/Angelo.app --into CANDIDATE   # macOS
    python3 final_runs.py init --output CAMPAIGN --candidate CANDIDATE --connections-dir DIR \\
        --claude-connection UUID --chatgpt-connection UUID (or --discover-connections) \\
        --codex-home DIR --stock-blender PATH --mcp-server-dir DIR --mcp-addon-dir DIR [--pilot-dir DIR]
    python3 final_runs.py plan --output CAMPAIGN            # order, commands and estimates; sends nothing
    python3 final_runs.py dry-run --output CAMPAIGN         # every step's unbilled modes (--depth plan|preflight|...)
    python3 final_runs.py headroom --output CAMPAIGN        # ChatGPT usage windows (no model request)
    python3 final_runs.py run --output CAMPAIGN --authorize-billed-runs [--only GLOB] [--phase gate]
        [--stop-after-hours H]
    python3 final_runs.py status --output CAMPAIGN          # also after an interruption; run again to resume
    python3 final_runs.py report --output CAMPAIGN
    python3 final_runs.py cache --output CAMPAIGN           # the cache-floor check of the finished gate steps
    python3 final_runs.py check-sign-in --connections-dir DIR  # a new machine's two subscriptions (existence only)
    python3 final_runs.py prepare-connections --connections-dir DIR  # create them, unsigned (the Windows VM)

The candidate's freeze is in ``final_runs_freeze``, the reading side (report,
headroom, sign-in check) in ``final_runs_report``, the MCP runtime carried to
the Windows VM in ``final_runs_mcp``; doc/angelo/final_runs.rst is the guide.
Run it from a clean checkout of the candidate's commit with the Python the
harnesses use (Blender's bundled 3.13 on macOS). Never touches a credential.
"""

import argparse
import fnmatch
import json
import os
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

import cache_floor  # noqa: E402
import final_runs_load as quiet  # noqa: E402
import final_runs_plan as campaign  # noqa: E402
from final_runs_freeze import RECEIPT, digest, executable, freeze, inventory, verify_candidate  # noqa: E402,F401
from final_runs_report import (chatgpt_window, check_sign_in, discover_connections, headroom,  # noqa: E402,F401
                                prepare_connections, report)

CONFIG, STATE, JOURNAL = "CONFIG.json", "STATE.json", "JOURNAL.jsonl"
EXIT_RATE_LIMITED = 3
COOLDOWN_S = 3600        # A blocked subscription is tried again after this long.
WINDOW_STOP_PERCENT = 95  # ChatGPT steps wait for the usage window's reset above this, so no trial is cut short.
MAX_PAUSE_S = 6 * 3600   # A machine busy longer than this stops the run (resume later).
BUSY_RETRY_S = 120       # run --ignore-load: the pause before a start a harness refused is tried again.


def platform_name():
    return "windows" if sys.platform == "win32" else "macos" if sys.platform == "darwin" else "linux"


def write_json(path, value):
    temporary = Path(str(path) + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, default=str))
    os.replace(temporary, path)


def journal(campaign_dir, event, **values):
    with (Path(campaign_dir) / JOURNAL).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(dict(event=event, at=time.strftime("%Y-%m-%dT%H:%M:%S%z"), **values)) + "\n")


# ------------------------------------------------------------------------ commands

def harness_python(config):
    return config.get("python") or sys.executable


def connection(step, config):
    """The step's subscription connection: the campaign's own (given or discovered at init), else the plan's."""
    found = (config.get("connections") or {}).get(step["account"]) or step["connection"]
    if not found:
        raise SystemExit("no %s connection for %s: give it at init (--%s-connection or ANGELO_FINAL_RUNS_%s_CONNECTION) "
                         "or use --discover-connections" % (step["account"], step["id"], step["account"],
                                                            step["account"].upper()))
    return found


def command(step, config, folder, *, billed=True, preflight=False):
    """The billed command of one step (one trial per call); with ``billed`` False its plan-only form, or with
    ``preflight`` the harness's own pre-start checks (executables, sign-ins, folder), which send nothing."""
    exe = json.loads((Path(config["candidate"]) / RECEIPT).read_text())["executable"]
    codex = step["runtime"] == "codex"
    if step["tool"] == "pilot":   # The pilot's own runner and interpreter (init --pilot-dir).
        pilot = config["pilot"]
        mode = (["measure", "--authorize-measured-trials", "--stop-file", str(Path(config["output"]) / "STOP")]
                if billed else ["smoke"])
        return ["nice", "-n", "10", pilot.get("python") or harness_python(config), "-B",
                str(Path(pilot["dir"]) / "run_pilot.py"), *mode, "--config", pilot["configs"][step["model_key"]],
                "--output", str(folder)]
    common = ["--connection-id", connection(step, config), "--model", step["model"],
              "--source-connections-dir", config["connections_dir"]]
    common += ["--codex-home", config["codex_home"]] if codex else []
    common += ["--authorize-billed-runs"] if billed else ["--preflight-only"] if preflight else ["--plan-only"]
    if step["tool"] == "eval":
        return [harness_python(config), str(HERE / "eval_live_agent.py"), *common, "--blender", exe,
                "--effort", "low", "--trials", "1", "--task-timeout", "300", "--observation", "turn-context",
                "--context-rebase-tokens", "0", "--input-isolation", "on", "--after-failure", "reset",
                "--task-set", step["task_set"], *step["args"], "--output", str(folder)]
    line = [harness_python(config), str(HERE / "bench_mcp_vs_angelo.py"), *common, "--runtime", step["runtime"],
            "--angelo-binary", exe, "--task-set", step["task_set"], "--trials", str(step["trials"]),
            "--sides", "both" if "mcp" in step["sides"] else "angelo", *campaign.COMMON,
            "--window-state", campaign.window_state(step), *step["args"],
            "--output", str(folder)]
    if "mcp" in step["sides"]:
        line += ["--stock-blender", config["stock_blender"], "--mcp-server-dir", config["mcp_server_dir"],
                 "--mcp-addon-dir", config["mcp_addon_dir"]]
        line += ["--codex", config["codex"]] if codex and config.get("codex") else []
        line += ["--claude", config["claude"]] if not codex and config.get("claude") else []
    return line + (["--resume", "--max-new-trials", "1"] if billed else [])


def pilot_entries(step):
    """The pilot's own counterbalanced order for one case (``preparation/pilot.py``: plan)."""
    index = campaign.PILOT_CASES.index(step["task_set"])
    return ["%s-%02d-%s" % (step["task_set"], trial, side) for trial in range(1, step["trials"] + 1)
            for side in (("angelo", "mcp") if (trial + index) % 2 else ("mcp", "angelo"))]


def completed(step, folder):
    """Finished trials (bench: per side; eval: the one trial; pilot: entries with a supervision record)."""
    import bench_mcp_vs_angelo as bench
    folder = Path(folder)
    if step["tool"] == "eval":   # One trial in the folder itself; a rate-limited one is not done.
        receipt = folder / "eval-live-agent.json"
        trial = (json.loads(receipt.read_text()).get("trials") or [{}])[0] if receipt.is_file() else None
        return {"angelo-1"} if trial is not None and trial.get("invalid") != "rate_limited" else set()
    if step["tool"] == "pilot":
        done = set()
        for entry in pilot_entries(step):
            receipt = folder / entry / "SUPERVISION.json"
            value = json.loads(receipt.read_text()) if receipt.is_file() else {}
            if value.get("completed_entries") == 1 and not value.get("stop_reason"):
                done.add(entry)
        return done
    return {"%s-%d" % (side, trial) for trial in range(1, step["trials"] + 1) for side in step["sides"]
            if bench.trial_done(folder, side, trial)}


def planned(step):
    if step["tool"] == "eval":
        return {"angelo-1"}
    if step["tool"] == "pilot":
        return set(pilot_entries(step))
    return {"%s-%d" % (side, trial) for trial in range(1, step["trials"] + 1) for side in step["sides"]}


# ------------------------------------------------------------------------ running

def run_unit(step, config, campaign_dir, folder, line, step_folder=None):
    """One trial (or pilot entry): wait for a quiet machine, run with the load sampled, record it.

    With ``config["ignore_load"]`` (``run --ignore-load``: an indicative snapshot on a busy machine) it does not
    wait; the machine's state at the start is recorded either way. ``folder`` is where the unit writes (a pilot
    entry's own folder); ``step_folder`` (default ``folder``) is where its completion is counted.
    """
    threshold = config["load_threshold"]
    waited = 0 if config.get("ignore_load") else quiet.wait_quiet(
        threshold, Path(campaign_dir) / JOURNAL, poll=60, max_wait=MAX_PAUSE_S, stop_file=Path(campaign_dir) / "STOP")
    at_start = quiet.check(threshold)
    step_folder = step_folder or folder
    before = completed(step, step_folder)
    folder.parent.mkdir(parents=True, exist_ok=True)
    log = Path(str(folder) + ".log")
    started = time.monotonic()
    own = []
    with quiet.Sampler(Path(str(folder) + "-load.jsonl"), threshold, own=own) as sampler, log.open("a") as stream:
        process = subprocess.Popen(line, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                   env=dict(os.environ, GIT_LFS_SKIP_SMUDGE="1", PYTHONDONTWRITEBYTECODE="1"))
        own.append(process.pid)
        code = process.wait()
    new = sorted(completed(step, step_folder) - before)
    load = sampler.summary()
    journal(campaign_dir, "trial", step=step["id"], trials=new, exit_code=code, waited_s=waited,
            elapsed_s=round(time.monotonic() - started, 1), load=load, at_start=at_start,
            load_guard="ignored" if config.get("ignore_load") else "enforced")
    return code, new, load


def set_aside(step, folder, campaign_dir, units, into):
    """Move finished units out of the way (kept, never deleted) so the next call runs them again."""
    import bench_mcp_vs_angelo as bench
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if step["tool"] == "bench":
        for unit in units:
            side, trial = unit.rsplit("-", 1)
            bench.set_aside(folder, side, int(trial), into=into)
        return
    target = (folder.parent / into / ("%s-%s" % (folder.name, stamp)) if step["tool"] == "pilot"
              else Path(campaign_dir) / into / ("%s-%s" % (step["id"], stamp)))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(folder), str(target))


def run_step(step, config, campaign_dir, state, deadline=None):
    """Every pending trial of one step; returns "done", "blocked" (rate limit), "failed" or "deadline"."""
    folder = Path(campaign_dir) / step["id"]
    verify_candidate(config)
    record = state["steps"].setdefault(step["id"], {"status": "pending", "trials": {}})
    reruns = record.setdefault("contended_reruns", {})
    busy_refusals = 0
    while planned(step) - completed(step, folder):
        if (Path(campaign_dir) / "STOP").exists():
            raise InterruptedError("STOP file present")
        if deadline is not None and time.time() >= deadline:
            return "deadline"
        target = folder
        if step["tool"] == "pilot":
            entry = sorted(planned(step) - completed(step, folder), key=pilot_entries(step).index)[0]
            target = folder / entry
            if target.exists():   # Cut short or rate-limited: kept, then run again.
                set_aside(step, target, campaign_dir, [entry], "interrupted")
            line = command(step, config, target) + ["--only-entry", entry]
        else:
            if step["tool"] == "eval" and folder.exists() and not completed(step, folder):
                set_aside(step, folder, campaign_dir, [], "interrupted")
            line = command(step, config, folder)
        code, new, load = run_unit(step, config, campaign_dir, target, line, step_folder=folder)
        key = ",".join(new)
        if new and load.get("contended") and reruns.get(key, 0) < config.get("contended_reruns", 1):
            reruns[key] = reruns.get(key, 0) + 1   # Another program competed for the machine: run it again.
            set_aside(step, target, campaign_dir, new, "contended")
            journal(campaign_dir, "contended_rerun", step=step["id"], trials=new, load=load)
            write_json(Path(campaign_dir) / STATE, state)
            continue
        for unit in new:
            record["trials"][unit] = {"exit_code": code, "load": load}
        write_json(Path(campaign_dir) / STATE, state)
        if code == EXIT_RATE_LIMITED:
            return "blocked"
        if not new and busy_refusals < 3 and (config.get("ignore_load")
                                              or not quiet.check(config["load_threshold"])["quiet"]):
            # The harness refused to start because something heavy started meanwhile (the pilot refuses any
            # build tool by name): wait for a quiet machine again rather than give the step up.
            busy_refusals += 1
            journal(campaign_dir, "start_refused_busy", step=step["id"], exit_code=code)
            if config.get("ignore_load"):
                time.sleep(BUSY_RETRY_S)   # No quiet wait to fall back on: give the competing build a moment.
            continue
        if not new:
            return "failed"   # A start or sign-in problem: nothing more is sent on this step.
    return "done"


def gate_passes(campaign_dir, step):
    """``(passed, runs)`` of a finished gate step, from the harness's own summary."""
    folder = Path(campaign_dir) / step["id"]
    if step["tool"] == "eval":
        grading = json.loads((folder / "eval-live-agent.json").read_text()).get("grading") or {}
        return grading.get("passed", 0), grading.get("total", 0)
    summary = json.loads((folder / "bench-mcp-vs-angelo.json").read_text())["summary"]["tasks"]
    return (sum(task.get("angelo", {}).get("passed", 0) for task in summary.values()),
            sum(task.get("angelo", {}).get("runs", 0) for task in summary.values()))


def run(config, campaign_dir, steps, *, continue_after_gate=False, sleep=time.sleep, deadline=None,
        retry_failed=False):
    """Every step in order, resuming from STATE.json; 0 finished or stopped at the deadline (resume later),
    1 a gate below its bar, 4 the machine stayed busy, 5 a STOP file."""
    state_path = Path(campaign_dir) / STATE
    state = json.loads(state_path.read_text()) if state_path.is_file() else {"steps": {}, "blocked": {}}
    # A failed step with --retry-failed, and a done step whose trial was set aside since (kept), run again.
    retried = [s["id"] for s in steps if state["steps"].get(s["id"], {}).get("status") == "failed" and retry_failed
               or state["steps"].get(s["id"], {}).get("status") == "done"
               and planned(s) - completed(s, Path(campaign_dir) / s["id"])]
    for step_id in retried:   # Finished trials stay; only the step's verdict is cleared.
        state["steps"][step_id]["status"] = "pending"
    journal(campaign_dir, "run", steps=[s["id"] for s in steps], load=quiet.load(), deadline=deadline,
            retried_failed=retried)
    while True:
        pending = [s for s in steps if state["steps"].get(s["id"], {}).get("status") not in ("done", "failed")]
        if not pending:
            break
        if any(s["phase"] == "h2h" for s in pending) and not continue_after_gate and not gates_clear(campaign_dir, steps, state):
            journal(campaign_dir, "stopped", reason="a gate step is below its bar; the head-to-head would be void")
            return 1
        now = time.time()
        if deadline is not None and now >= deadline:
            journal(campaign_dir, "deadline", pending=[s["id"] for s in pending])
            return 0
        runnable = [s for s in pending if state["blocked"].get(s["account"], 0) <= now]
        if not runnable:
            until = min(state["blocked"].values())
            journal(campaign_dir, "waiting_for_limits", until=until)
            sleep(max(60, min(until, deadline or until) - now))
            continue
        step = runnable[0]
        window = chatgpt_window(config) if step["account"] == "chatgpt" else None
        if window is not None:   # Calibrates the estimates and keeps the window from ending inside a trial.
            journal(campaign_dir, "chatgpt_window", used_percent=window[0], resets_at=window[1])
            if window[0] >= WINDOW_STOP_PERCENT:
                state["blocked"]["chatgpt"] = window[1]
                write_json(state_path, state)
                continue
        journal(campaign_dir, "step", step=step["id"], estimate=campaign.estimate(step))
        try:
            status = run_step(step, config, campaign_dir, state, deadline)
        except TimeoutError as busy:
            journal(campaign_dir, "stopped", reason=str(busy))
            write_json(state_path, state)
            return 4
        except InterruptedError:
            journal(campaign_dir, "stopped", reason="STOP file")
            write_json(state_path, state)
            return 5
        state["steps"][step["id"]]["status"] = status if status not in ("blocked", "deadline") else "pending"
        if status == "blocked":
            state["blocked"][step["account"]] = time.time() + COOLDOWN_S
            journal(campaign_dir, "rate_limited", account=step["account"], step=step["id"], cooldown_s=COOLDOWN_S)
        write_json(state_path, state)
        if status == "deadline":
            journal(campaign_dir, "deadline", pending=[s["id"] for s in pending])
            return 0
    cache = cache_check(campaign_dir, steps, state)
    if cache and cache["sev"]:
        journal(campaign_dir, "cache_sev", breaches=cache["breaches"])
        print("\n".join(cache_floor.describe(cache)), file=sys.stderr)
        return 1
    journal(campaign_dir, "finished")
    return 0


def gates_clear(campaign_dir, steps, state):
    """Every gate step finished at its bar (30/30, 120/120, 7/7, 8/8) with no cache SEV; unfinished gates are not
    clear."""
    for step in steps:
        if step["phase"] != "gate":
            continue
        if state["steps"].get(step["id"], {}).get("status") != "done":
            return False
        passed, runs = gate_passes(campaign_dir, step)
        if not runs or passed != runs:
            return False
    cache = cache_check(campaign_dir, steps, state)
    return not (cache and cache["sev"])


def cache_check(campaign_dir, steps, state):
    """The cache-floor check (``cache_floor``) over the finished gate steps, or None when none has finished.

    A gate step whose prompt-cache read fell below its recorded floor is a cache SEV: the gate fails although every
    task passed, because a prefix or cache-key break multiplies input cost by up to 20.
    """
    folders = [Path(campaign_dir) / step["id"] for step in steps
               if step["phase"] == "gate" and state["steps"].get(step["id"], {}).get("status") == "done"]
    return cache_floor.check_targets(folders) if folders else None


# ------------------------------------------------------------------------ CLI

def load_config(campaign_dir):
    return json.loads((Path(campaign_dir) / CONFIG).read_text())


def select(steps, only=(), phase="all"):
    return [s for s in steps if (phase == "all" or s["phase"] == phase)
            and (not only or any(fnmatch.fnmatch(s["id"], pattern) for pattern in only))]


def show_plan(config, steps):
    rows = []
    for step in steps:
        rows.append({"id": step["id"], "account": step["account"], "model": step["model"], "tool": step["tool"],
                     "task_set": step["task_set"], "trials": step["trials"], "sides": list(step["sides"]),
                     "estimate": campaign.estimate(step),
                     "command": command(step, config, Path(config["output"]) / step["id"])})
    return {"platform": config["platform"], "load_threshold": config["load_threshold"], "steps": rows,
            "totals": campaign.summary(steps), "estimate_sources": campaign.SOURCES}


DEPTHS = ("plan", "preflight", "launch", "full")


def scripted_clients(config, line):
    """The client executables a scripted run adds to its step's command: it drives both MCP clients, Claude Code
    and Codex, whatever the step's own runtime, and a step names only its own. Without them the other client was
    the harness's default (macOS paths: the ChatGPT app's codex, ``~/.local/bin/claude``), which a Windows machine
    does not have: its codex side started an empty program name (WinError 87)."""
    return [arg for flag in ("--codex", "--claude") if flag not in line and config.get(flag[2:])
            for arg in (flag, config[flag[2:]])]


def dry_run(config, steps, campaign_dir, depth="full", ignore_load=False, wait_minutes=0):
    """Unbilled checks of every step, deeper with ``depth``: ``plan`` each step's plan-only form; ``preflight``
    also each harness's pre-start checks (executables, sign-ins, connection, folder); ``launch`` also each
    runtime's MCP side started with no model; ``full`` also every one-shot set's scripted-model run on both
    sides and the pilot's no-provider smoke."""
    if wait_minutes and not ignore_load:
        try:
            quiet.wait_quiet(config["load_threshold"], Path(campaign_dir) / JOURNAL, poll=30, max_wait=wait_minutes * 60)
        except TimeoutError:
            pass   # Refused below with the reasons.
    state = quiet.check(config["load_threshold"])
    journal(campaign_dir, "dry_run", depth=depth, ignore_load=ignore_load, machine=state)
    if not state["quiet"] and not ignore_load:
        print("REFUSED (machine busy): " + "; ".join(state["reasons"]), file=sys.stderr)
        return 2
    level = DEPTHS.index(depth)
    folder = Path(campaign_dir) / "dry-run" / time.strftime("%Y%m%d-%H%M%S")   # Each dry run its own new folders.
    folder.mkdir(parents=True)
    jobs = [(step["id"], command(step, config, folder / step["id"], billed=False)) for step in steps
            if step["tool"] != "pilot"]
    if level >= 1:
        jobs += [("preflight-" + step["id"], command(step, config, folder / ("preflight-" + step["id"]), billed=False,
                                                     preflight=True)) for step in steps if step["tool"] != "pilot"]
    if level >= 2:
        # The operations sets' MCP side (a one-shot set's sends one message; its scripted run below covers it).
        for runtime, step in {s["runtime"]: s for s in steps if "mcp" in s["sides"] and s["tool"] == "bench"
                              and s["kind"] in ("default", "long")}.items():
            line = [x for x in command(step, config, folder / ("mcp-launch-" + runtime), billed=False)
                    if x != "--plan-only"]
            jobs.append(("mcp-launch-" + runtime, line + ["--dry-run-mcp"]))
    if level >= 3:
        for task_set, step in {s["task_set"]: s for s in steps if s["kind"] in ("mini", "scene", "heavy")}.items():
            line = [x for x in command(step, config, folder / ("scripted-" + task_set), billed=False)
                    if x not in ("--plan-only",)]
            jobs.append(("scripted-" + task_set, line + scripted_clients(config, line) + ["--dry-run-scenario"]))
        for model, step in {s["model_key"]: s for s in steps if s["tool"] == "pilot"}.items():
            entry = pilot_entries(step)[0]
            busy = ["--allow-competing-load"] if ignore_load else []   # The smoke records them instead of refusing.
            jobs.append(("pilot-smoke-" + model, command(step, config, folder / ("pilot-smoke-" + model), billed=False)
                         + ["--only-entry", entry] + busy))
    failures = 0
    for name, line in jobs:
        with (folder / (name + ".log")).open("w") as log:
            code = subprocess.run(line, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                  env=dict(os.environ, GIT_LFS_SKIP_SMUDGE="1")).returncode
        failures += code != 0
        journal(campaign_dir, "dry_run_job", job=name, exit_code=code)
        print("%-56s exit %s" % (name, code), flush=True)
    print("%d jobs, %d failed; logs in %s" % (len(jobs), failures, folder), flush=True)
    return 0 if not failures else 1


def parse(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("action", choices=("freeze", "init", "plan", "dry-run", "run", "status", "report", "headroom",
                                           "check-sign-in", "prepare-connections", "cache"))
    parser.add_argument("--output", help="the campaign folder (init creates it)")
    parser.add_argument("--source", help="freeze: the built app to freeze")
    parser.add_argument("--into", help="freeze: a new folder for the frozen copy")
    parser.add_argument("--candidate", help="init: a freeze folder (RECEIPT.json)")
    parser.add_argument("--platform", default=platform_name(), choices=("macos", "windows", "linux"))
    for name in ("connections-dir", "codex-home", "stock-blender", "mcp-server-dir", "mcp-addon-dir", "pilot-dir",
                 "codex", "claude", "python"):
        parser.add_argument("--" + name, default="")
    parser.add_argument("--account", action="append", choices=("claude", "chatgpt"), default=[],
                        help="check-sign-in: only these accounts must be signed in (the machine's steps use only them)")
    parser.add_argument("--load-threshold", type=float, default=quiet.DEFAULT_THRESHOLD)
    parser.add_argument("--pilot-python", default="", help="init: the pilot's interpreter (it ran on Python 3.14)")
    parser.add_argument("--rehearsal", action="store_true", help="init: a rehearsal campaign (never run billed)")
    parser.add_argument("--contended-reruns", type=int, default=1, help="init: runs again of a contended trial")
    parser.add_argument("--claude-connection", default=campaign.TEST_CONNECTIONS["claude"], help="init")
    parser.add_argument("--chatgpt-connection", default=campaign.TEST_CONNECTIONS["chatgpt"], help="init")
    parser.add_argument("--discover-connections", action="store_true",
                        help="init: use the one Claude and the one ChatGPT subscription in --connections-dir "
                             "(a new machine's sign-in, e.g. the Windows VM's); --codex-home follows the ChatGPT one")
    parser.add_argument("--stop-after-hours", type=float, default=0, help="run: start no trial after this long")
    parser.add_argument("--wait-quiet-minutes", type=float, default=0,
                        help="dry-run: wait up to this long for a quiet machine before refusing")
    parser.add_argument("--only", action="append", default=[], metavar="GLOB", help="only the steps matching")
    parser.add_argument("--phase", choices=("all", "gate", "h2h"), default="all")
    parser.add_argument("--depth", choices=DEPTHS, default="full", help="dry-run depth (see dry_run)")
    parser.add_argument("--ignore-load", action="store_true",
                        help="dry-run, run: start on a busy machine (recorded; run's results are indicative only)")
    parser.add_argument("--max-trials", type=int, default=0,
                        help="at most this many trials a step (an indicative snapshot; its own campaign folder)")
    parser.add_argument("--authorize-billed-runs", action="store_true")
    parser.add_argument("--continue-after-gate-failure", action="store_true")
    parser.add_argument("--retry-failed", action="store_true",
                        help="run: try the steps a start problem stopped again (finished trials are kept)")
    return parser.parse_args(argv)


def pilot_mismatches(config, receipt):
    """Why the pilot's configs would not run the frozen candidate with the campaign's stock Blender and MCP side."""
    problems = []
    for model, path in config["pilot"]["configs"].items():
        pilot = json.loads(Path(path).read_text())
        if digest(pilot["angelo_binary"]) != receipt["executable_sha256"]:
            problems.append("%s: its Angelo is not the candidate (freeze_runtime.py, make_config.py)" % model)
        if digest(pilot["stock_blender"]) != digest(config["stock_blender"]):
            problems.append("%s: its stock Blender differs from --stock-blender" % model)
        if pilot.get("runtime_rehearsal") and not config.get("rehearsal"):
            problems.append("%s: a rehearsal freeze (measure refuses it)" % model)
    return problems


def init(args):
    campaign_dir = Path(args.output).resolve()
    receipt = json.loads((Path(args.candidate).resolve() / RECEIPT).read_text())
    connections = ({"claude": args.claude_connection, "chatgpt": args.chatgpt_connection}
                   if not args.discover_connections else discover_connections(args.connections_dir))
    unnamed = [account for account in ("claude", "chatgpt") if not connections.get(account)]
    if unnamed:
        raise SystemExit("init: no connection id for %s: pass --claude-connection and --chatgpt-connection (or set "
                         "ANGELO_FINAL_RUNS_CLAUDE_CONNECTION and ANGELO_FINAL_RUNS_CHATGPT_CONNECTION), or "
                         "--discover-connections" % " and ".join(unnamed))
    codex_home = args.codex_home or str(Path(args.connections_dir) / "codex-profile" / connections["chatgpt"])
    checkout = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True)
    config = {"platform": args.platform, "output": str(campaign_dir), "candidate": str(Path(args.candidate).resolve()),
              "candidate_executable": receipt["executable"], "connections_dir": args.connections_dir,
              "connections": connections, "codex_home": codex_home, "stock_blender": args.stock_blender,
              "mcp_server_dir": args.mcp_server_dir, "mcp_addon_dir": args.mcp_addon_dir, "codex": args.codex,
              "claude": args.claude, "python": args.python or sys.executable, "load_threshold": args.load_threshold,
              "contended_reruns": args.contended_reruns, "rehearsal": args.rehearsal,
              # The Windows VM's tree is an extracted archive: its commit comes from the stage.
              "checkout": checkout.stdout.strip() if checkout.returncode == 0 else os.environ.get("ANGELO_SOURCE_COMMIT", "")}
    if args.pilot_dir:
        pilot = Path(args.pilot_dir).resolve()
        config["pilot"] = {"dir": str(pilot), "configs": {m: str(pilot / "configs" / ("%s.json" % m))
                                                          for m in ("sol", "astra")},
                           "python": args.pilot_python or args.python or sys.executable}
        mismatched = pilot_mismatches(config, receipt)
        if mismatched:
            raise SystemExit("init: the pilot's configs do not run this candidate: " + "; ".join(mismatched))
    missing = [key for key in ("connections_dir", "codex_home", "stock_blender", "mcp_server_dir", "mcp_addon_dir")
               if not config[key] or not Path(config[key]).exists()]
    if missing:
        raise SystemExit("init: missing or not found: " + ", ".join(missing))
    campaign_dir.mkdir(parents=True, exist_ok=True)
    if (campaign_dir / CONFIG).exists():
        raise SystemExit("init: %s already has a campaign; resume it with run" % campaign_dir)
    # Fixed before any trial: every step's trials per side (arm). The report flags an arm whose n differs, so a
    # campaign cut short, a --max-trials run or a rerun that added trials is never read as the planned comparison.
    config["planned_n"] = planned_n(campaign.campaign(args.platform))
    write_json(campaign_dir / CONFIG, config)
    return config


def planned_n(steps):
    """``{step id: {side: trials}}``: the n per arm a campaign plans (pilot entries: one per side and trial)."""
    return {step["id"]: {side: step["trials"] for side in step["sides"]} for step in steps}


def main(argv=None):
    args = parse(sys.argv[1:] if argv is None else argv)
    if args.action == "freeze":
        print(json.dumps(freeze(args.source, args.into), indent=2))
        return 0
    if args.action == "init":
        print(json.dumps(init(args), indent=2))
        return 0
    if args.action == "prepare-connections":   # A new machine's two bench connections; no sign-in, nothing read.
        print(json.dumps(prepare_connections(args.connections_dir), indent=2))
        return 0
    if args.action == "check-sign-in":   # Existence of the sign-in files only; nothing is read or sent.
        try:
            result = check_sign_in(args.connections_dir, accounts=args.account or ("claude", "chatgpt"))
        except SystemExit as problem:
            print(problem, file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2))
        return 0 if result["ready"] else 2
    campaign_dir = Path(args.output).resolve()
    config = load_config(campaign_dir)
    steps = select(campaign.campaign(config["platform"]), args.only, args.phase)
    steps = [s for s in steps if s["tool"] != "pilot" or config.get("pilot")]
    if args.max_trials:
        steps = [dict(s, trials=min(s["trials"], args.max_trials)) for s in steps]
    if args.action == "plan":
        print(json.dumps(show_plan(config, steps), indent=2))
        return 0
    if args.action == "dry-run":
        return dry_run(config, steps, campaign_dir, args.depth, args.ignore_load, args.wait_quiet_minutes)
    if args.action == "headroom":
        print(json.dumps(headroom(config), indent=2, default=str))
        return 0
    if args.action == "status":
        state_path = campaign_dir / STATE
        state = json.loads(state_path.read_text()) if state_path.is_file() else {"steps": {}}
        for step in steps:
            done = len(completed(step, campaign_dir / step["id"]))
            print("%-44s %-8s %d/%d" % (step["id"], state["steps"].get(step["id"], {}).get("status", "pending"),
                                        done, len(planned(step))))
        return 0
    if args.action == "report":
        print(json.dumps(report(config, steps, campaign_dir), indent=2))
        return 0
    if args.action == "cache":   # Reads the receipts only; exit 1 on a cache SEV.
        state_path = campaign_dir / STATE
        cache = cache_check(campaign_dir, steps, json.loads(state_path.read_text()) if state_path.is_file()
                            else {"steps": {}})
        print("\n".join(cache_floor.describe(cache)) if cache else "cache floors: no finished gate step")
        return 1 if cache and cache["sev"] else 0
    if not args.authorize_billed_runs:
        print("REFUSED: run sends billed messages on the test subscriptions; pass --authorize-billed-runs",
              file=sys.stderr)
        return 2
    if subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no"], capture_output=True,
                      text=True).stdout.strip():
        print("REFUSED: the checkout has uncommitted changes; the harness Python must be the campaign's commit",
              file=sys.stderr)
        return 2
    if config.get("rehearsal"):
        print("REFUSED: a rehearsal campaign is never run billed; init a new campaign on the frozen candidate",
              file=sys.stderr)
        return 2
    verify_candidate(config, full=True)
    if args.ignore_load:
        config["ignore_load"] = True
        journal(campaign_dir, "load_guard_ignored", load=quiet.load(),
                note="indicative run on a busy machine: timings are not the quiet-machine measurement")
    deadline = time.time() + args.stop_after_hours * 3600 if args.stop_after_hours else None
    return run(config, campaign_dir, steps, continue_after_gate=args.continue_after_gate_failure, deadline=deadline,
               retry_failed=args.retry_failed)


if __name__ == "__main__":
    sys.exit(main())
