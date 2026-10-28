# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The unbilled end-to-end dry run of a scenario task set (``bench_mcp_vs_angelo.py --dry-run-scenario``).

Every part of a billed scenario run is real except the model, which is a
local script that builds the ground truth turn by turn:

* ``mcp-trial-01``: stock Blender, Blender Lab's MCP add-on and server, and the
  real Claude Code CLI pointed at ``scenario_fake_models.FakeMessages`` (a
  fresh profile and a dummy key; nothing reaches Anthropic);
* ``mcp-trial-02``: the same with the real ``codex app-server`` and
  ``codex_prompt_probe.FakeResponses`` (a fresh, signed-out CODEX_HOME);
* ``angelo-trial-01``: the Angelo binary with ``scenario_dry_run_session.py``,
  which runs the unchanged trial driver behind a scripted runtime.

Then the bench's own summary (``gather`` and ``finish``: SUMMARY.md, the
results JSON, the scorecard and both report files) is built exactly as after
a billed run. The dry run passes only when every side passed every turn,
each side's model input carried the reference images byte-for-byte (SHA-256
per turn), the end of the session saved, captured and scored (IoU and depth
agreement at least 0.99 against the ground truth), the Undo probe and
hygiene report ran, and, with ``--demo-record``, the Angelo recording became
a 1080p H.264 video with a poster. ``dry-run.json`` lists each check.

For a one-shot test the scripted models (``oneshot_fake_models``) also render
the still the brief asks for and look at their own result, all within the
one message, and ``oneshot_report.dry_run_checks`` adds its checks per side.
"""

import json
import os
import sys
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for _path in (str(ROOT / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import bench_mcp_codex as codex_mcp  # noqa: E402
import bench_mcp_trial as mcp  # noqa: E402
import bench_workdirs as workdirs  # noqa: E402
import eval_live_agent  # noqa: E402
import eval_send_record as send_record  # noqa: E402  (the trial's record, read and written as the launcher's)
import oneshot_fake_models as oneshot_fake  # noqa: E402
import scenario_fake_models as fake  # noqa: E402
import scenario_references as references  # noqa: E402
import scenario_scorecard as scorecard  # noqa: E402
import scenario_report as report  # noqa: E402
import scenario_specs as specs  # noqa: E402

SIDES = ("mcp-claude", "mcp-codex", "angelo")
SCORE_FLOOR = 0.99


def _images_match(bodies, tasks, name):
    """Per turn: whether the turn's reference hashes appear, unchanged, in the recorded model requests."""
    seen = set()
    for raw in bodies:
        seen.update(fake.image_hashes(raw))
    output = {}
    for task in tasks:
        expected = [digest for _, _, digest in specs.reference_files(name, task["references"])]
        output[task["id"]] = {"expected": len(expected), "found": sum(digest in seen for digest in expected)}
    return output


def run_mcp_claude(name, tasks, output, base, bodies=None, warm_up=False):
    """``bodies``: a list that receives the request bodies the fake endpoint recorded (diagnostic_checks).

    ``warm_up``: first the bench's warm-up through this trial's launch (``bench_warm_up.mcp_side``), against its own
    fake endpoint, so its first request can be compared with the trial's (``warm_up_checks``).
    """
    requests = output / "mcp-trial-01-requests"
    server = (oneshot_fake.OneShotMessages if specs.is_oneshot(name) else fake.FakeMessages)(requests, name, tasks)
    profile = output / "mcp-trial-01-claude-profile"
    profile.mkdir(parents=True)
    config = dict(base, claude_config_dir=str(profile), claude_cwd=str(workdirs.root(output) / "mcp-cwd-claude"),
                  fake_model_environment={"ANTHROPIC_BASE_URL": server.url, "ANTHROPIC_API_KEY": "fake-not-a-secret",
                                          "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1",
                                          "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})
    if warm_up:
        import bench_warm_up
        import eval_warm_up
        warm = fake.FakeMessages(output / eval_warm_up.FOLDER / "mcp-requests", name, [])
        try:
            bench_warm_up.mcp_side(dict(config, fake_model_environment=dict(
                config["fake_model_environment"], ANTHROPIC_BASE_URL=warm.url)), output / eval_warm_up.FOLDER / "mcp-01",
                {key: value for key, value in base.items() if key == "debug_thinking"})
        finally:
            warm.close()
    try:
        outcome = mcp.run_trial(config, 1, output / "mcp-trial-01")
    finally:
        server.close()
    if bodies is not None:
        bodies.extend(server.bodies)
    return outcome, _images_match(server.bodies, tasks, name)


def run_mcp_codex(name, tasks, output, base, warm_up=False, bodies=None):
    """``warm_up``: first the bench's warm-up through this trial's launch (``bench_warm_up.mcp_codex_side``) against
    its own fake endpoint; ``bodies`` receives the trial's request bodies (``diagnostic_checks``)."""
    from codex_prompt_probe import FakeResponses
    requests = output / "mcp-trial-02-requests"
    requests.mkdir(parents=True)
    server = FakeResponses(requests, (oneshot_fake if specs.is_oneshot(name) else fake).codex_script(name, tasks))
    home = output / "mcp-trial-02-codex-home"
    home.mkdir(parents=True)
    config = dict(base, runtime="codex", codex_home=str(home), claude_cwd=str(workdirs.root(output) / "mcp-cwd-codex"))
    if warm_up:
        import bench_warm_up
        import eval_warm_up
        (output / eval_warm_up.FOLDER / "mcp-requests-codex").mkdir(parents=True)
        warm = FakeResponses(output / eval_warm_up.FOLDER / "mcp-requests-codex", [])
        try:
            bench_warm_up.mcp_codex_side(config, output / eval_warm_up.FOLDER / "mcp-02",
                                         {key: value for key, value in base.items() if key == "debug_thinking"},
                                         fake_base_url="http://127.0.0.1:%d/v1" % warm.port)
        finally:
            warm.server.shutdown()
    try:
        outcome = codex_mcp.run_trial(config, 2, output / "mcp-trial-02",
                                      fake_base_url="http://127.0.0.1:%d/v1" % server.port, tasks=tasks)
    finally:
        server.server.shutdown()
    if bodies is not None:
        bodies.extend(server.bodies)
    return outcome, _images_match(server.bodies, tasks, name)


def ask_why_checks(output, sides):
    """With ``--ask-why-on-miss``: each asked side recorded its follow-up, and the Undo probe ran after it."""
    checks = {}
    if "mcp-claude" in sides:
        trial = json.loads((output / "mcp-trial-01" / "trial.json").read_text())
        checks["mcp-01: the ask-why follow-up was asked and recorded"] = (
            (trial.get("ask_why") or {}).get("asked") is True and (output / "mcp-trial-01" / "why.md").is_file())
        checks["mcp-01: the Undo probe ran after it"] = bool((trial.get("scenario") or {}).get("undo_after_ask_why"))
    if "angelo" in sides:
        report = (send_record.read(output / "angelo-trial-01" / send_record.NAME).get("trials") or [{}])[0]
        asked = report.get("ask_why") or {}
        checks["angelo-01: the ask-why follow-up was answered in Read only, the scene unchanged"] = (
            asked.get("outcome") == "answered" and asked.get("scene_unchanged") is True
            and (output / "angelo-trial-01" / "trial-01" / "qa" / "why.md").is_file())
        checks["angelo-01: the Undo probe ran after it"] = bool((report.get("scenario") or {}).get("undo_after_ask_why"))
    return checks


def run_angelo(task_set, output, binary, task_timeout, demo_record=False, ask_why=False, diagnostics=None,
               folder=None, warm_up=False, runtime="claude"):
    """The real Angelo binary and trial driver behind the scripted runtime (a fake Codex record, no sign-in).

    ``diagnostics`` with debug thinking on a Claude run: a fake Claude subscription record instead, so the flag
    reaches a Claude chat's controller; the scripted runtime then records the Claude launch
    that chat's request would make (``scenario_dry_run_session``, ``CLAUDE_LAUNCH``) without starting it.
    ``warm_up``: the bench's warm-up message instead of the trial (``eval_warm_up``), into ``folder``, on a Claude
    record too, so its launch can be compared with the trial's.
    """
    from angelo_agent.connections import ConnectionStore
    diagnostics = dict(diagnostics or {})
    # A Codex run's chat stays a Codex one (its scripted runtime records the Codex opening: CODEX_LAUNCH).
    claude = runtime == "claude" and bool(diagnostics.get("debug_thinking") or warm_up)
    folder = folder or output / "angelo-trial-01"
    folder.mkdir(parents=True)
    source = folder / "source-accounts"
    record = ConnectionStore(source).add("ANTHROPIC" if claude else "OPENAI", "SUBSCRIPTION",
                                         name="Scenario dry-run scripted runtime")
    connection = eval_live_agent.isolated_connection(source, record["id"], folder / "accounts")
    scratch = folder / "isolation"
    for name in ("home", "tmp"):
        (scratch / name).mkdir(parents=True)
    args = types.SimpleNamespace(
        task_set=task_set, long_tasks=40, observation="legacy", context_rebase_tokens=0, after_failure="skip",
        cache_ttl="", approve_delegation="", python_approvals="approve", connection_id=record["id"], team_mode="",
        task_timeout=task_timeout, effort="low", model="", input_isolation="on", stable_state_dir="",
        demo_record=demo_record, ask_why_on_miss=ask_why, debug_thinking=bool(diagnostics.get("debug_thinking")),
        warm_up_only=warm_up)
    saved_script, saved_environment = eval_live_agent.SESSION_SCRIPT, dict(os.environ)
    eval_live_agent.SESSION_SCRIPT = HERE / "scenario_dry_run_session.py"
    # The dry run needs no sign-in, so it is isolated completely, HOME and the macOS user folder included.
    os.environ.update(ANGELO_SCENARIO_DRY_RUN="1", HOME=str(scratch / "home"), CFFIXED_USER_HOME=str(scratch / "home"),
                      TMPDIR=str(scratch / "tmp"), **{specs.REFERENCES_ENV: str(specs.reference_root())},
                      **({"ANGELO_EVAL_ASK_WHY_FORCE": "1"} if ask_why else {}))
    try:
        trial = eval_live_agent.run_trial(Path(binary), folder, folder / "accounts", args, 1, len(specs.tasks(
            specs.scenario_id(task_set))))
    finally:
        eval_live_agent.SESSION_SCRIPT = saved_script
        os.environ.clear()
        os.environ.update(saved_environment)
    plan = {"dry_run": True, "task_set": task_set}
    send_record.write(folder / send_record.NAME, eval_live_agent.aggregate(plan, connection, [trial]))
    images_file = folder / "trial-01" / "qa" / "dry-run-images.json"
    images = json.loads(images_file.read_text()) if images_file.is_file() else {}
    return trial, {turn: {"expected": len(entry["expected"]), "found": len(entry["expected"]) if entry["match"] else 0}
                   for turn, entry in images.items()}


# What the scripted runtime writes when the chat is a Claude one (scenario_dry_run_session.record_launch).
CLAUDE_LAUNCH = "dry-run-claude-launch.json"


def diagnostic_checks(output, sides, diagnostics, mcp_bodies=(), runtime="claude", codex_bodies=()):
    """Debug thinking and the MCP side's recorded request parameters, per side."""
    import mcp_request_parameters
    checks = {}
    if "mcp-claude" in sides:
        trial = json.loads((output / "mcp-trial-01" / "trial.json").read_text())
        recorded = [task.get("request_parameters") or {} for task in trial.get("tasks") or []]
        sent = [mcp_request_parameters.body_parameters(raw) for raw in mcp_bodies]
        sent = [entry for entry in sent if entry != (None, None)]
        display = "summarized" if diagnostics.get("debug_thinking") else None
        checks["mcp-01: request parameters recorded (effort, thinking display)"] = bool(recorded) and all(
            entry.get("effort") and entry.get("thinking_display_observed") for entry in recorded)
        checks["mcp-01: recorded effort and display match the requests sent"] = bool(sent) and all(
            effort == recorded[0].get("effort") for _, effort in sent) and all(
            set(entry.get("thinking_display_observed") or {}) <= {value or "unrecorded" for value, _ in sent}
            for entry in recorded)
        if diagnostics:
            import eval_diagnostics
            stream = output / "mcp-trial-01" / "claude-stream.jsonl"
            checks["mcp-01: its raw stream opens with the diagnostic mark"] = stream.is_file() and (
                eval_diagnostics.TRANSCRIPT_MARK in stream.read_text(encoding="utf-8").split("\n", 1)[0])
        if display:
            checks["mcp-01: Claude Code launched with --thinking-display summarized"] = (
                "--thinking-display" in trial.get("claude_command", [])
                and trial["claude_command"][trial["claude_command"].index("--thinking-display") + 1] == display)
            checks["mcp-01: every request asked for summarized thinking"] = bool(sent) and all(
                value == display for value, _ in sent)
    if "mcp-codex" in sides and runtime == "codex":
        checks.update(codex_checks(output, diagnostics, codex_bodies))
    if "angelo" in sides and runtime == "codex" and diagnostics:
        report = (send_record.read(output / "angelo-trial-01" / send_record.NAME).get("trials")
                  or [{}])[0]
        stamped = report.get("diagnostics") or {}
        checks["angelo-01: the Codex trial report carries the diagnostic stamp"] = bool(stamped.get("claim")) and all(
            stamped.get(key) == value for key, value in diagnostics.items() if key == "debug_thinking")
        checks["angelo-01: the chat's Codex opening was recorded"] = (
            output / "angelo-trial-01" / "trial-01" / "qa" / CODEX_LAUNCH).is_file()
    elif "angelo" in sides and diagnostics.get("debug_thinking"):
        report = (send_record.read(output / "angelo-trial-01" / send_record.NAME).get("trials")
                  or [{}])[0]
        stamped = report.get("diagnostics") or {}
        path = output / "angelo-trial-01" / "trial-01" / "qa" / CLAUDE_LAUNCH
        launches = json.loads(path.read_text()) if path.is_file() else []
        checks["angelo-01: the trial report carries the diagnostic stamp"] = bool(stamped.get("claim")) and all(
            stamped.get(key) == value for key, value in diagnostics.items() if key == "debug_thinking")
        checks["angelo-01: the chat's Claude launch was recorded"] = bool(launches)
        for launch in launches:
            if diagnostics.get("debug_thinking"):
                checks["angelo-01: the launch asks for summarized thinking"] = (
                    launch.get("thinking_display") == "summarized")
    return checks


CODEX_LAUNCH = "dry-run-codex-launch.json"   # scenario_dry_run_session.CODEX_LAUNCH


def codex_checks(output, diagnostics, bodies):
    """A Codex run's MCP side: its recorded request parameters, and with debug thinking the summaries it asked for."""
    import eval_diagnostics
    trial = json.loads((output / "mcp-trial-02" / "trial.json").read_text())
    recorded = [task.get("request_parameters") or {} for task in trial.get("tasks") or []]
    sent = [json.loads(raw) for raw in bodies]
    checks = {"mcp-02: request parameters recorded (effort, reasoning summary, per request)": bool(recorded) and all(
        entry.get("effort") and entry.get("requests") for entry in recorded)}
    if diagnostics.get("debug_thinking"):
        checks["mcp-02: every request asked for reasoning summaries"] = bool(sent) and all(
            (body.get("reasoning") or {}).get("summary") == "auto" for body in sent)
        stream = output / "mcp-trial-02" / "codex-stream.jsonl"
        checks["mcp-02: its raw stream opens with the diagnostic mark"] = stream.is_file() and (
            eval_diagnostics.TRANSCRIPT_MARK in stream.read_text(encoding="utf-8").split("\n", 1)[0])
    return checks


# The prefix of a Responses request a warm-up and its trial share: everything but the conversation and the
# per-thread cache key (stock Codex keys its prompt cache by thread ID).
CODEX_PER_THREAD = ("input", "prompt_cache_key", "client_metadata")


def _codex_first(folder, needle):
    for path in sorted(Path(folder).glob("request-*.json")):
        body = json.loads(path.read_text())
        if needle in json.dumps(body.get("input") or [], ensure_ascii=False):
            return body
    return None


def _first(folder, needle):
    """The first recorded Messages request in ``folder`` whose text has ``needle`` (a turn's prompt)."""
    for path in sorted(Path(folder).glob("request-*.json")):
        body = json.loads(path.read_text())
        if needle in json.dumps(body.get("messages") or [], ensure_ascii=False):
            return body
    return None


# Claude Code's first system block: "x-anthropic-billing-header: cc_version=<version>.<3 hex>; ...", whose suffix
# follows the first user message (the same brief gives the same suffix on either side, in any session or mode). It
# carries no cache_control and is billing attribution, not the cached prompt: a trial's first request reads the
# tools-and-system prefix that another brief's run wrote (in recorded mini one-shot runs, each brief's first MCP
# trial read 10,700 tokens from the cache). A warm-up, with its own message, cannot match it and need not.
BILLING_HEADER = "x-anthropic-billing-header:"


def _prefix(body):
    """A request's tools and system blocks, without the per-message billing header block."""
    system = [block for block in body.get("system") or []
              if not str(block.get("text", "") if isinstance(block, dict) else block).startswith(BILLING_HEADER)]
    return json.dumps(body.get("tools")), json.dumps(system)


def warm_up_checks(output, sides, card, tasks, runtime="claude"):
    """The warm-up wrote the trial's own prefix (system and tools; on Angelo the whole Claude launch, or the Codex
    opening its cacheAffinityKey is derived from) and no trial."""
    import eval_warm_up
    checks, folder = {}, output / eval_warm_up.FOLDER
    if runtime == "codex":
        if "mcp-codex" in sides:
            warm = _codex_first(folder / "mcp-requests-codex", eval_warm_up.PROMPT)
            trial = _codex_first(output / "mcp-trial-02-requests", tasks[0]["prompt"][:80])
            keys = sorted(set(warm or {}) | set(trial or {}))
            differing = [key for key in keys if key not in CODEX_PER_THREAD
                         and (warm or {}).get(key) != (trial or {}).get(key)]
            checks["mcp-02: the warm-up's instructions, tools and settings are the trial's first request's"] = bool(
                warm and trial) and not differing
        if "angelo" in sides:
            def opening(path):
                return json.loads(path.read_text())[0] if path.is_file() else {}
            warm = opening(folder / "angelo-01" / "trial-01" / "qa" / CODEX_LAUNCH)
            trial = opening(output / "angelo-trial-01" / "trial-01" / "qa" / CODEX_LAUNCH)
            checks["angelo-01: the warm-up's Codex opening is the trial's (the cacheAffinityKey's inputs)"] = bool(
                warm and trial) and all(warm.get(key) == trial.get(key) for key in ("model", "prefix", "turn_options"))
            report = (send_record.read(folder / "angelo-01" / send_record.NAME).get("trials") or [{}])[0]
            checks["angelo-01: the warm-up was recorded as a warm-up, with no task"] = (
                (report.get("warm_up") or {}).get("status") == "completed" and report.get("tasks") == [])
        checks["warm-up: no trial, summary or scorecard counts it"] = not list(folder.glob("*-trial-*")) and all(
            "warm" not in "%s-%s" % (trial["side"], trial["trial"]) for trial in card["trials"])
        return checks
    if "mcp-claude" in sides:
        warm = _first(folder / "mcp-requests", eval_warm_up.PROMPT)
        trial = _first(output / "mcp-trial-01-requests", tasks[0]["prompt"][:80])
        checks["mcp-01: the warm-up's tools and system are the trial's first request's, byte for byte"] = bool(
            warm and trial) and _prefix(warm) == _prefix(trial)
    if "angelo" in sides:
        def launch(path):
            return json.loads(path.read_text())[0] if path.is_file() else {}
        warm = launch(folder / "angelo-01" / "trial-01" / "qa" / CLAUDE_LAUNCH)
        trial = launch(output / "angelo-trial-01" / "trial-01" / "qa" / CLAUDE_LAUNCH)
        keys = ("command", "initialize_sha256", "schema_bytes", "instructions_sha256", "allowed_tools",
                "thinking_display")
        checks["angelo-01: the warm-up's Claude launch is the trial's (command, schema, instructions)"] = bool(
            warm and trial) and all(warm.get(key) == trial.get(key) for key in keys)
        report = (send_record.read(folder / "angelo-01" / send_record.NAME).get("trials") or [{}])[0]
        checks["angelo-01: the warm-up was recorded as a warm-up, with no task"] = (
            (report.get("warm_up") or {}).get("status") == "completed" and report.get("tasks") == [])
    checks["warm-up: no trial, summary or scorecard counts it"] = not list(folder.glob("*-trial-*")) and all(
        "warm" not in "%s-%s" % (trial["side"], trial["trial"]) for trial in card["trials"])
    return checks


def verdict(card, images, tasks, demo_record=False):
    checks = {}
    for trial in card["trials"]:
        key = "%s-%02d" % (trial["side"], trial["trial"])
        checks[key + ": every turn passed"] = (trial["metrics"]["turns_passed"] == len(tasks)
                                               and trial["metrics"]["turns_attempted"] == len(tasks))
        sent = images.get(key, {})
        checks[key + ": references reached the model input unchanged"] = bool(sent) and all(
            entry["found"] == entry["expected"] for turn, entry in sent.items()) and len(sent) == len(tasks)
        scores = (trial.get("scores") or {}).get("mean") or {}
        checks[key + ": end-of-session scores match the ground truth"] = all(
            (scores.get(metric) or 0) >= SCORE_FLOOR for metric in ("scene_iou", "subject_iou", "depth_agreement"))
        checks[key + ": end-of-session compliance"] = (
            (trial.get("end_of_session_checks") or {}).get("passed") == (trial.get("end_of_session_checks") or {}).get("total"))
        checks[key + ": undo probe and hygiene ran"] = bool(trial.get("undo")) and trial.get("hygiene") is not None
        if trial["side"] == "angelo" and demo_record:
            demo = trial.get("demo") or {}
            checks[key + ": demo video and poster encoded"] = bool(demo.get("video")) and bool(demo.get("poster")) \
                and not demo.get("error")
    return checks


def run(task_set, output, *, angelo_binary, stock_blender, mcp_server_dir, mcp_addon_dir, claude, codex,
        sides=SIDES, task_timeout=300., demo_record=False, summarize=None, diagnostics=None, warm_up=False,
        runtime="claude"):
    """The dry run; ``summarize(output, mcp_trials)`` is the bench's own gather-and-finish (a billed run's path).

    ``diagnostics`` (``eval_diagnostics.stamp``): ``debug_thinking`` reaches the MCP side's real Claude Code (its
    launch argument is then proven against the fake endpoint; Angelo's scripted runtime has no thinking), and
    ``ask_why_on_miss`` the Claude MCP side and Angelo, asked even though the scripted models pass every check
    (``eval_ask_why.FORCE_ENV``), so the follow-up, its record and the late Undo probe run in the real app.
    """
    diagnostics = dict(diagnostics or {})
    name = specs.scenario_id(task_set)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    manifest = references.make(name, stock_blender)
    tasks = specs.tasks(name)
    base = {"stock_blender": stock_blender, "mcp_server_dir": mcp_server_dir, "mcp_addon_dir": mcp_addon_dir,
            "claude": claude, "codex": codex, "model": "claude-opus-5-5", "effort": "low",
            "task_timeout": task_timeout, "task_set": task_set, "long_tasks": 40, "input_isolation": "on",
            "after_failure": "skip", "runtime": "claude", "codex_home": ""}
    images, outcomes, bodies, codex_bodies = {}, {}, [], []
    codex = runtime == "codex"   # A Codex run: the warm-up and debug thinking apply to the Codex MCP side.
    if "mcp-claude" in sides:
        asking = {"ask_why_force": True} if diagnostics.get("ask_why_on_miss") else {}
        outcomes["mcp-claude"], images["mcp-01"] = run_mcp_claude(name, tasks, output, dict(base, **diagnostics,
                                                                                            **asking), bodies,
                                                                  warm_up and not codex)
    if "mcp-codex" in sides:
        thinking = {"debug_thinking": True} if codex and diagnostics.get("debug_thinking") else {}
        outcomes["mcp-codex"], images["mcp-02"] = run_mcp_codex(name, tasks, output, dict(
            base, model="gpt-6.1-sol", **thinking), warm_up and codex, codex_bodies)
    if "angelo" in sides and warm_up:   # The bench's warm-up through the trial's launch, before the trial.
        import eval_warm_up
        run_angelo(task_set, output, angelo_binary, task_timeout, diagnostics=diagnostics,
                   folder=output / eval_warm_up.FOLDER / "angelo-01", warm_up=True, runtime=runtime)
    if "angelo" in sides:
        outcomes["angelo"], images["angelo-01"] = run_angelo(task_set, output, angelo_binary, task_timeout,
                                                             demo_record, diagnostics.get("ask_why_on_miss", False),
                                                             diagnostics, runtime=runtime)
    if summarize is not None:
        summarize(output, 2)
        card = json.loads((output / "scenario-scorecard.json").read_text())
    else:
        card = scorecard.collect(output, task_set, blender=stock_blender)
        report.write(output, card, {"finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                    "dry_run_scenario": True})
    checks = verdict(card, images, tasks, demo_record)
    if diagnostics.get("ask_why_on_miss"):
        checks.update(ask_why_checks(output, sides))
    checks.update(diagnostic_checks(output, sides, diagnostics, bodies, runtime, codex_bodies))
    if warm_up:
        checks.update(warm_up_checks(output, sides, card, tasks, runtime))
    if specs.is_oneshot(task_set) and summarize is not None:
        import oneshot_report   # One message, the saved still, the pixel checks and the path metrics, per side.
        checks.update(oneshot_report.dry_run_checks(output))
    result = {"success": bool(checks) and all(checks.values()), "checks": checks, "images": images,
              "paid_requests": 0, "elapsed_s": round(time.monotonic() - started, 1),
              "references": {"folder": str(specs.references_dir(name)), "digest": manifest["digest"]},
              "outcomes": {side: {k: v for k, v in outcome.items() if k in ("success", "aborted", "error")}
                           for side, outcome in outcomes.items()}}
    (output / "dry-run.json").write_text(json.dumps(result, indent=2, default=str))
    return result
