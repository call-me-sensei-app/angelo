# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The release measurement campaign: its steps in run order, their commands and their estimates (final_runs.py).

Phase ``gate`` (the release gate, Angelo only, in Auto, the product default):
gate set v2 on Opus 5.5, GPT-6.1 Sol and GPT-6 Astra, the default set (6 tasks
x 5 trials = 30) and the long set (40 tasks x 3 trials = 120), then the live
smoke (7 gate tasks and 8 survey questions) and an Ask smoke (the default set,
1 trial, ``--angelo-approval ask``). Phase ``h2h`` (five-trial head to
head against stock Blender + the pilot's MCP add-on, sides alternating per
trial, matched model, effort, prompts, scenes; for Angelo the product's own
Always allow scripts setting, as MCP's scripts run without a prompt, so every
report says "scripts allowed"): gate default and long, the pilot's four
Object Mode cases, the five mini one-shots, the small scenes footbridge,
bridge, tree and cottage, and the heavy cases: the castle one-shot
(``oneshot-castle``) and five everyday edits in a dense castle scene of about
2,000 objects and 2.7 million vertices (``scenario-heavy``). Sol and Astra run
everything; Opus the operations (default, long), the minis and the heavy cases;
Sol and Astra also run the default set as returning
users (``returning-*``: one Angelo profile over three launches, a new chat or
Codex thread per task), beside the cold-start columns. The pilot cases need a Codex runtime on both
sides (its adapter has no Claude path) and run on macOS only.

Order: the gates come first, because a gate below its bar changes the candidate
and would void every later run; inside each phase the steps alternate between
the two subscriptions (Claude for Opus, ChatGPT for Sol and Astra), so one
account's usage window recovers while the other runs, and cheap, short steps
come before long and expensive ones, so an early stop still leaves results.

Estimates are rough per-prompt planning numbers, the same for both sides
(``SOURCES``); they size a campaign and are not results.

A campaign scoped to steps without an MCP side (the gates and smokes: Angelo's side alone) needs no stock Blender and
no MCP add-on or server: ``mcp_side`` says whether a selection does, and ``python3 final_runs_plan.py needs --platform P
[--only GLOB ...]`` prints it, so a machine's preparation installs them only when a selected step runs them.
"""

import argparse
import fnmatch
import json
import os

import gui_launch

# The connection ids of the campaign's two accounts (one Claude, one ChatGPT) in the connections folder. Nothing
# defaults them: init takes --claude-connection and --chatgpt-connection, these environment variables, or
# --discover-connections, and a step without one refuses to build its command (final_runs.connection).
TEST_CONNECTIONS = {"claude": os.environ.get("ANGELO_FINAL_RUNS_CLAUDE_CONNECTION", ""),
                    "chatgpt": os.environ.get("ANGELO_FINAL_RUNS_CHATGPT_CONNECTION", ""),
                    "gemini": os.environ.get("ANGELO_FINAL_RUNS_GEMINI_CONNECTION", "")}
SUBSCRIPTIONS = ("claude", "chatgpt")
# A pay-per-token API-key account: its one connection runs in place from a connections folder holding only it (its
# key is in that folder's OS key-store item, entered in the app by the account's owner: eval_live_agent's
# isolated_connection), billed with --allow-api-key-connection and no ChatGPT --codex-home. {account: its mode}.
API_KEY_ACCOUNTS = {"gemini": "gemini_api"}
MODELS = {"opus": {"model": "claude-opus-5-5", "runtime": "claude", "account": "claude"},
          "sol": {"model": "gpt-6.1-sol", "runtime": "codex", "account": "chatgpt"},
          "astra": {"model": "gpt-6-astra", "runtime": "codex", "account": "chatgpt"},
          # Gemini 4 Carbon on the runtime's Gemini wire (agent_runtime.json wire_apis), at its shipping effort. Its gate
          # of record is the Windows MSI's (maintainer ruling 2026-11-13); a model without "platforms" gates on every one.
          "carbon": {"model": "gemini-4-carbon", "runtime": "codex", "account": "gemini", "platforms": ("windows",)}}
GATED = ("sol", "opus", "astra", "carbon")   # Gates of record (run order); head-to-heads: the first three only.
PILOT_CASES = ("batch_edits", "duplication", "material_assignment", "node_editing")
MINIS = ("oneshot-mini-footbridge", "oneshot-mini-footbridge-arched", "oneshot-mini-cube", "oneshot-mini-camera",
         "oneshot-mini-lighting")
# The footbridge and the bridge are their unambiguous briefs; their -conflict variants, the original
# briefs, are regression checks of Angelo alone and never part of the campaign.
SCENES = ("oneshot-footbridge", "oneshot-bridge", "oneshot-tree", "oneshot-cottage")
# Every model, both sides: the castle one-shot (kind "scene") and the dense scene's five edits (kind "heavy").
HEAVY = ("oneshot-castle", "scenario-heavy")
H2H_TRIALS = 5
RETURNING_LAUNCHES = 3   # Launches on one profile: cold start, then two returning-user relaunches.
# Every head-to-head and gate step: one stable working folder, the chat editor's observation, real input ignored
# in the trial windows, every task graded. No effort: a gate runs each model at the app's shipping effort
# (maintainer, 2026-11-12: "Shipping default (high)"); a head-to-head adds H2H_EFFORT, one effort for both sides.
COMMON = ["--angelo-stable-workspace", "--angelo-observation", "turn-context", "--input-isolation", "on"]
H2H_EFFORT = ["--effort", "low"]


def window_state(step):
    """A step's window state (gui_launch): when both sides run, the one they share on this platform (front on macOS,
    where stock Blender opens in front whatever the flag); one side alone stays behind the person's applications."""
    return gui_launch.matched_window_state() if "mcp" in step["sides"] else "behind"

# Rough planning estimates for sizing a campaign's time, requests and list-price cost, the same for
# both sides and rounded up: they are not results. Per prompt and side: (wall s, input tokens, cached
# input tokens, output tokens, model requests).
_ESTIMATE = {
    ('default', 'claude'): (6.0, 72000, 68000, 400, 4.0),
    ('default', 'codex'): (10.0, 58000, 49000, 200, 4.0),
    ('long', 'claude'): (5.0, 90000, 89000, 400, 4.0),
    ('long', 'codex'): (9.0, 61000, 58000, 190, 4.0),
    ('pilot', 'codex'): (40.0, 128000, 104000, 820, 6.0),
    ('mini', 'claude'): (60.0, 100000, 80000, 2000, 6.0),
    ('mini', 'codex'): (60.0, 100000, 80000, 2000, 6.0),
    ('scene', 'codex'): (216.0, 427000, 382000, 9100, 15.0),
    ('scene', 'claude'): (216.0, 427000, 382000, 9100, 15.0),   # assumed: as the Codex scenes
    # The heavy scene's turns, from one measured Opus trial a side (the larger side's values);
    # Codex's wall scaled as the default set's (assumed).
    ('heavy', 'claude'): (18.0, 145000, 138000, 1100, 4.0),
    ('heavy', 'codex'): (30.0, 145000, 138000, 1100, 4.0),
}
PER_PROMPT = {(kind, side, runtime): value for (kind, runtime), value in _ESTIMATE.items()
              for side in ("angelo", "mcp")}
# The bridge: assumed for its longer brief and caps. The castle: a measured Astra trial's MCP side took about the
# time, tokens and requests of one Codex scene, so it keeps the scene estimate.
SCENE_SCALE = {"oneshot-bridge": 1.5, "oneshot-castle": 1.0}
BOOT_S = {"angelo": 20.0, "mcp": 15.0, "pilot": 30.0}  # assumed per-trial start, grading and cleanup
SOURCES = ("rough planning estimates, the same for both sides; a campaign's report measures the real values",)


# The gates of record test the product as shipped: parallel edit left to each trial's own capability check, never
# forced on or off (release-106). Added here, so no gate step can be built without it or with a switch of its own.
AS_SHIPPED = {"bench": "--angelo-parallel-as-shipped", "eval": "--parallel-as-shipped"}
PARALLEL_SWITCHES = ("--angelo-parallel-agents", "--angelo-parallel-as-shipped", "--parallel-agents",
                     "--parallel-as-shipped")


def _step(id, phase, model, tool, task_set, trials, sides, prompts, kind, args=()):
    facts = MODELS[model]
    args = list(args)
    if phase == "gate":
        if any(arg in PARALLEL_SWITCHES for arg in args):
            raise ValueError("gate step %s sets parallel edit itself; gates run it as shipped" % id)
        args.append(AS_SHIPPED[tool])   # A gate tool without an as-shipped form fails here, never silently off.
    return {"id": id, "phase": phase, "model_key": model, "model": facts["model"], "runtime": facts["runtime"],
            "account": facts["account"], "connection": TEST_CONNECTIONS[facts["account"]], "tool": tool,
            "task_set": task_set, "trials": trials, "sides": sides, "prompts": prompts, "kind": kind,
            "args": args}


def gate_steps(model):
    return [
        _step("gate-%s-default" % model, "gate", model, "bench", "default", 5, ("angelo",), {"angelo": 6}, "default",
              ["--after-failure", "reset"]),
        _step("gate-%s-long" % model, "gate", model, "bench", "long", 3, ("angelo",), {"angelo": 40}, "long",
              ["--long-tasks", "40", "--after-failure", "reset"]),
        _step("smoke-%s-gate7" % model, "gate", model, "eval", "long", 1, ("angelo",), {"angelo": 7}, "long",
              ["--long-tasks", "7"]),
        _step("smoke-%s-survey" % model, "gate", model, "eval", "survey-questions", 1, ("angelo",), {"angelo": 8},
              "long"),
        # The gate runs in Auto, the product default; one cheap trial of the default set in Ask keeps that mode,
        # where every typed edit waits for the tester, exercised before a release.
        _step("smoke-%s-ask" % model, "gate", model, "bench", "default", 1, ("angelo",), {"angelo": 6}, "default",
              ["--after-failure", "reset", "--angelo-approval", "ask"]),
    ]


def connection_args(step, config, connection_id):
    """The harness arguments that name a step's connection: a subscription from the campaign's connections folder
    (a Codex runtime with the ChatGPT profile), an API-key account's from its own folder, in place."""
    if step["account"] in API_KEY_ACCOUNTS:
        folder = (config.get("api_key_dirs") or {}).get(step["account"])
        if not folder:
            raise SystemExit("no %s connections folder for %s: give it at init (--%s-connections-dir)"
                             % (step["account"], step["id"], step["account"]))
        allow = "--allow-api-key-connection" if step["tool"] == "eval" else "--angelo-allow-api-key-connection"
        return ["--connection-id", connection_id, "--source-connections-dir", folder, allow]
    found = ["--connection-id", connection_id, "--source-connections-dir", config["connections_dir"]]
    return found + (["--codex-home", config["codex_home"]] if step["runtime"] == "codex" else [])


def accounts(steps):
    """The accounts these steps bill, in the plan's order."""
    used = {step["account"] for step in steps}
    return [account for account in TEST_CONNECTIONS if account in used]


def h2h_steps(model, platform):
    both = ("mcp", "angelo")
    steps = [_step("h2h-%s-default" % model, "h2h", model, "bench", "default", H2H_TRIALS, both,
                   {"mcp": 5, "angelo": 6}, "default", ["--after-failure", "reset", "--angelo-always-allow-scripts"])]
    if MODELS[model]["runtime"] == "codex":
        # The returning-user column (doc/angelo/final_runs.rst): one Angelo profile across RETURNING_LAUNCHES
        # launches, a new chat (MCP: a new Codex thread) per task; the first task of launch 1 is the cold start.
        steps.append(_step("returning-%s-default" % model, "h2h", model, "bench", "default", RETURNING_LAUNCHES, both,
                           {"mcp": 5, "angelo": 6}, "default",
                           ["--after-failure", "reset", "--angelo-always-allow-scripts", "--returning-user"]))
    if MODELS[model]["runtime"] == "codex" and platform == "macos":
        steps += [_step("pilot-%s-%s" % (model, case), "h2h", model, "pilot", case, H2H_TRIALS, both,
                        {"mcp": 1, "angelo": 1}, "pilot") for case in PILOT_CASES]
    steps += [_step("h2h-%s-%s" % (model, mini), "h2h", model, "bench", mini, H2H_TRIALS, both,
                    {"mcp": 1, "angelo": 1}, "mini", ["--angelo-always-allow-scripts"]) for mini in MINIS]
    steps += [_step("h2h-%s-long" % model, "h2h", model, "bench", "long", H2H_TRIALS, both, {"mcp": 40, "angelo": 40},
                    "long", ["--long-tasks", "40", "--after-failure", "reset", "--angelo-always-allow-scripts"])]
    if MODELS[model]["runtime"] == "codex":
        steps += [_step("h2h-%s-%s" % (model, scene), "h2h", model, "bench", scene, H2H_TRIALS, both,
                        {"mcp": 1, "angelo": 1}, "scene", ["--angelo-always-allow-scripts"]) for scene in SCENES]
    steps += [_step("h2h-%s-oneshot-castle" % model, "h2h", model, "bench", "oneshot-castle", H2H_TRIALS, both,
                    {"mcp": 1, "angelo": 1}, "scene", ["--angelo-always-allow-scripts"]),
              _step("h2h-%s-scenario-heavy" % model, "h2h", model, "bench", "scenario-heavy", H2H_TRIALS, both,
                    {"mcp": 5, "angelo": 5}, "heavy", ["--angelo-always-allow-scripts"])]
    return steps


def campaign(platform="macos"):
    """Every step in run order (module docstring): gates first, then the head-to-heads, accounts alternating."""
    gates = [step for model in GATED if platform in MODELS[model].get("platforms", (platform,))
             for step in gate_steps(model)]
    short, heavy = {}, {}
    for model in ("sol", "opus", "astra"):
        steps = h2h_steps(model, platform)
        short[model] = [step for step in steps if step["kind"] in ("default", "pilot", "mini")]
        heavy[model] = [step for step in steps if step["kind"] in ("long", "scene", "heavy")]
    # ChatGPT, Claude, ChatGPT, Claude: each account's usage window recovers while the other runs; the long and
    # scene runs, the largest share of the ChatGPT usage, come last.
    return (gates + short["sol"] + short["opus"] + short["astra"] + heavy["opus"] + heavy["sol"]
            + heavy["astra"])


def _rates(model):
    from angelo_agent import model_profiles
    return model_profiles.rates(model, "1h")


def estimate(step):
    """Planned prompts, model requests, tokens, list-price USD and hours of one step."""
    totals = {"prompts": 0, "requests": 0., "input": 0, "cached": 0, "output": 0, "usd": 0., "seconds": 0.}
    rates = _rates(step["model"])
    for side in step["sides"]:
        wall, prompt_in, cached, output, calls = PER_PROMPT[(step["kind"], side, step["runtime"])]
        scale = SCENE_SCALE.get(step["task_set"], 1.0)
        count = step["prompts"][side] * step["trials"]
        totals["prompts"] += count
        totals["requests"] += count * calls * scale
        totals["input"] += int(count * prompt_in * scale)
        totals["cached"] += int(count * cached * scale)
        totals["output"] += int(count * output * scale)
        totals["seconds"] += count * wall * scale + step["trials"] * BOOT_S["pilot" if step["tool"] == "pilot" else side]
    fresh = totals["input"] - totals["cached"]
    totals["usd"] = round((fresh * rates["input"] + totals["cached"] * rates["cache_read"]
                           + totals["output"] * rates["output"]) / 1e6, 2)
    totals["requests"] = round(totals["requests"])
    totals["hours"] = round(totals["seconds"] / 3600, 2)
    return totals


def summary(steps):
    """Totals by model and by account (subscription), and in run order the hours each account is busy."""
    by_model, by_account = {}, {}
    for step in steps:
        values = estimate(step)
        for table, key in ((by_model, step["model"]), (by_account, step["account"])):
            entry = table.setdefault(key, {"steps": 0, "prompts": 0, "requests": 0, "input": 0, "cached": 0,
                                           "output": 0, "usd": 0., "hours": 0.})
            entry["steps"] += 1
            for name in ("prompts", "requests", "input", "cached", "output", "usd", "hours"):
                entry[name] = round(entry[name] + values[name], 2)
    return {"by_model": by_model, "by_account": by_account,
            "hours": round(sum(entry["hours"] for entry in by_model.values()), 1),
            "usd": round(sum(entry["usd"] for entry in by_model.values()), 2)}


def select(steps, only=(), phase="all"):
    """The steps ``--only`` (globs over step ids, any of them) and ``--phase`` select; no glob selects every step."""
    return [s for s in steps if (phase == "all" or s["phase"] == phase)
            and (not only or any(fnmatch.fnmatch(s["id"], pattern) for pattern in only))]


def mcp_side(steps):
    """Whether any of ``steps`` runs the MCP side (stock Blender, the MCP add-on and server)."""
    return any("mcp" in step["sides"] for step in steps)


def main(argv=None):
    parser = argparse.ArgumentParser(description="What a campaign selection needs on a machine (no campaign needed).")
    parser.add_argument("action", choices=("needs",))
    parser.add_argument("--platform", default="macos", choices=("macos", "windows", "linux"))
    parser.add_argument("--only", action="append", default=[], metavar="GLOB")
    args = parser.parse_args(argv)
    steps = select(campaign(args.platform), args.only)
    billed = accounts(steps)   # api_key_accounts: the ones whose key the machine loads (bench_api_key.py).
    print(json.dumps({"platform": args.platform, "only": args.only, "steps": [s["id"] for s in steps],
                      "mcp_side": mcp_side(steps), "accounts": billed,
                      "api_key_accounts": [account for account in billed if account in API_KEY_ACCOUNTS]}))
    return 0 if steps else 2


if __name__ == "__main__":
    raise SystemExit(main())
