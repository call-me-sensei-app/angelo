# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One BILLED Blender MCP + Codex trial for ``bench_mcp_vs_angelo.py --runtime codex``.

The Codex counterpart of ``bench_mcp_trial.py`` (same stock Blender, private
add-on copy, port range, settle, grading and record fields). The runtime is
``codex app-server`` (the same binary Angelo's Codex connections run) with
Blender Lab's installed MCP server as its only MCP server:

* ``-c mcp_servers.blender.*`` points the server at the trial's port, with
  ``default_tools_approval_mode="approve"`` so no MCP call waits for a person;
* ``approval_policy="never"`` and ``sandbox_mode="read-only"``: any shell
  command runs read-only, nothing prompts; every other Codex default is kept
  (the product as shipped, as the Claude side keeps Claude Code's defaults);
* an ephemeral thread, ``CODEX_SQLITE_HOME`` inside the trial folder, and a
  dedicated, signed-in ``CODEX_HOME`` (``--codex-home``), never ``~/.codex``.
  Its ``config.toml`` must declare no MCP server of its own.

One thread per trial; each task is one ``turn/start``. Wall time runs from
``turn/start`` to ``turn/completed``. Provider limits come from a failed turn's
``codexErrorInfo`` or an exhausted ``account/rateLimits/updated`` window
(``eval_provider_limits.codex_limit``); the plan type those updates name is
recorded. ``fake_base_url`` (tests and ``--dry-run-mcp-codex``) replaces the
model with a local Responses endpoint: nothing is billed and no sign-in is used.
"""

import json
import os
import queue
import sys
import time
import traceback
from pathlib import Path

import bench_integrity
import bench_mcp_answers as answer_check
import bench_mcp_grading as grading
import bench_mcp_scenario as scenario
import bench_mcp_trial as trial_base
import bench_workdirs as workdirs
import eval_gate
import eval_grading
import eval_live_tasks as live
import eval_provider_limits as limits
import mcp_request_parameters
from angelo_agent.cli_runtime import CODEX_BUNDLE  # (standard library only)

SERVER = trial_base.SERVER_NAME


def default_codex(platform=None):
    """The ChatGPT app's bundled codex on macOS (the bundle Angelo's Codex connections also find); else none."""
    return CODEX_BUNDLE if (sys.platform if platform is None else platform) == "darwin" else ""


DEFAULT_CODEX = default_codex()
ITEM_TOOL_TYPES = ("mcpToolCall", "commandExecution", "webSearch", "dynamicToolCall", "fileChange")
# Codex starts MCP servers with the thread, asynchronously. A turn whose code runs before the Blender
# server is ready finds no Blender tool ("tools.mcp__blender__... is not a function"; seen on a loaded
# machine), so the first turn waits for the server's ready (or failed) status, as Claude Code connects
# its MCP servers before the first request. A runtime that reports no status within
# MCP_STATUS_GRACE_S is not waited for.
MCP_READY_TIMEOUT_S, MCP_STATUS_GRACE_S = 60., 5.
INITIALIZE_TIMEOUT_S = 300.
# --debug-thinking: the reasoning-summary request Angelo's Codex chats always send (providers, ``summary="auto"``), so
# both sides ask for the same summaries; without it the MCP side keeps Codex's own default.
REASONING_SUMMARY = "auto"


def reasoning_summary(config):
    """``turn/start``'s summary parameter for a diagnostic run's turns, else nothing."""
    return {"summary": REASONING_SUMMARY} if config.get("debug_thinking") else {}


def _toml(value):
    """A TOML value for ``-c key=value`` (strings, booleans, lists and flat inline tables)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        return "{" + ",".join("%s=%s" % (key, _toml(item)) for key, item in value.items()) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_toml(item) for item in value) + "]"
    return json.dumps(str(value))


def overrides(config, port, fake_base_url=None):
    """Process-local configuration for one trial; nothing is written to ``CODEX_HOME``."""
    server = trial_base.mcp_config(config, port)["mcpServers"][SERVER]
    values = {"approval_policy": "never", "sandbox_mode": "read-only", "model": config["model"],
              "model_reasoning_effort": config["effort"],
              "mcp_servers.%s.command" % SERVER: server["command"], "mcp_servers.%s.args" % SERVER: server["args"],
              "mcp_servers.%s.env" % SERVER: server["env"],
              "mcp_servers.%s.default_tools_approval_mode" % SERVER: "approve"}
    if fake_base_url:
        values.update({"model_provider": "angelo_fake", "model_providers.angelo_fake.name": "fake",
                       "model_providers.angelo_fake.base_url": fake_base_url,
                       "model_providers.angelo_fake.env_key": "ANGELO_FAKE_KEY",
                       "model_providers.angelo_fake.wire_api": "responses",
                       "model_providers.angelo_fake.requires_openai_auth": False,
                       "model_providers.angelo_fake.supports_websockets": False})
    return values


def codex_command(config, port, fake_base_url=None):
    command = [config["codex"], "app-server", "--listen", "stdio://"]
    for key, value in overrides(config, port, fake_base_url).items():
        command += ["-c", "%s=%s" % (key, _toml(value))]
    return command


def environment(config, folder, fake_base_url=None):
    extra = {"CODEX_HOME": config["codex_home"], "CODEX_SQLITE_HOME": folder / "codex-sqlite",
             "TMPDIR": folder / "tmp"}
    if fake_base_url:
        extra["ANGELO_FAKE_KEY"] = "fake-not-a-secret"
    return trial_base.clean_environment(**extra)


class CodexStream:
    """One ``codex app-server`` and its trial thread; every notification is timestamped on arrival."""

    def __init__(self, command, env, cwd, folder, config):
        self.command, self.env, self.cwd, self.config = command, env, cwd, config
        self.raw = (folder / "codex-stream.jsonl").open("w", encoding="utf-8")
        self.lines, self.rpc, self.spawned = queue.Queue(), None, None
        self.thread_id = self.turn_id = None
        self.unexpected_requests, self.account = [], None
        self.mcp_status, self.mcp_ready_ms = None, None

    @property
    def process(self):
        return self.rpc

    def start(self):
        from angelo_agent.protocol import JsonRpcProcess
        self.spawned = time.monotonic()
        self.rpc = JsonRpcProcess(self.command, env=self.env, cwd=str(self.cwd), on_event=self._event,
                                  on_request=self._request)
        # A loaded machine can take minutes to start the app-server; that time still counts as the first message's.
        self.rpc.request("initialize", {"clientInfo": {"name": "angelo_bench_mcp", "title": "Angelo bench",
                                                       "version": "1"}, "capabilities": {"experimentalApi": True}},
                         timeout=INITIALIZE_TIMEOUT_S)
        self.rpc.notify("initialized")
        account = (self.rpc.request("account/read", {"refreshToken": False}) or {}).get("account") or {}
        # The account type and plan only: never an e-mail address or token.
        self.account = {key: account.get(key) for key in ("type", "planType") if key in account}
        result = self.new_thread()
        self.wait_for_mcp_server()
        return result

    def new_thread(self):
        """A new conversation on this app-server (also each task's own with ``new_thread_per_task``)."""
        result = self.rpc.request("thread/start", {
            "cwd": str(self.cwd), "model": self.config["model"], "approvalPolicy": "never", "sandbox": "read-only",
            "serviceTier": "default", "ephemeral": True})
        self.thread_id = result["thread"]["id"]
        return result

    def wait_for_mcp_server(self):
        """Block until the Blender MCP server reports ready or failed (see MCP_READY_TIMEOUT_S)."""
        started = time.monotonic()
        while self.mcp_status not in ("ready", "failed"):
            waited = time.monotonic() - started
            if waited > MCP_READY_TIMEOUT_S or (self.mcp_status is None and waited > MCP_STATUS_GRACE_S):
                break
            time.sleep(.02)
        self.mcp_ready_ms = round((time.monotonic() - started) * 1000, 3)

    def _event(self, method, params):
        if method == "mcpServer/startupStatus/updated" and (params or {}).get("name") == SERVER:
            self.mcp_status = params.get("status")
        self.lines.put((time.monotonic(), {"method": method, "params": params}))

    def _request(self, identity, method, params):
        # approval_policy="never" and approved MCP tools: nothing should ask.
        self.unexpected_requests.append(method)
        self.lines.put((time.monotonic(), {"request": method, "params": params}))
        self.rpc.write({"id": identity, "error": {"code": -32601, "message": "No person answers in a benchmark"}})

    def send(self, text, images=()):
        """One turn; scenario turns attach their reference images as local image inputs."""
        response = self.rpc.request("turn/start", {"threadId": self.thread_id, "input": scenario.codex_input(text, images),
                                                   "effort": self.config["effort"], "model": self.config["model"],
                                                   **reasoning_summary(self.config)})
        self.turn_id = response["turn"]["id"]

    def interrupt(self):
        try:
            self.rpc.request("turn/interrupt", {"threadId": self.thread_id, "turnId": self.turn_id}, timeout=15)
        except Exception:  # The turn may have ended meanwhile; the collect grace still applies.
            pass

    def collect(self, deadline, task):
        """This turn's notifications up to its ``turn/completed``; returns (events, completed, problem)."""
        events = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return events, None, "deadline"
            try:
                stamp, message = self.lines.get(timeout=min(remaining, 1.))
            except queue.Empty:
                if self.rpc.closed.is_set():
                    return events, None, "exited"
                continue
            events.append((stamp, message))
            self.raw.write(json.dumps({"t_s": round(stamp - self.spawned, 4), "task": task, "message": message},
                                      default=str) + "\n")
            params = message.get("params") or {}
            if message.get("method") == "turn/completed" and (params.get("turn") or {}).get("id") == self.turn_id:
                self.raw.flush()
                return events, params, None

    def close(self):
        if self.rpc is not None:
            self.rpc.process.terminate()
            trial_base.stop_process(self.rpc.process, grace=15.)
        self.raw.close()


def analyze_codex_events(events, start):
    """Timings and steps of one Codex turn, in the Claude analysis's field names (bench_mcp_report)."""
    first_text = first_tool = None
    requests, tools, rate_limits, usage_last = 0, {}, [], []
    for stamp, message in events:
        method, params = message.get("method"), message.get("params") or {}
        item = params.get("item") if isinstance(params.get("item"), dict) else {}
        if method == "item/agentMessage/delta" and first_text is None:
            first_text = round((stamp - start) * 1000, 3)
        elif method == "item/started" and item.get("type") in ITEM_TOOL_TYPES:
            first_tool = round((stamp - start) * 1000, 3) if first_tool is None else first_tool
            name = "mcp__%s__%s" % (item.get("server"), item.get("tool")) if item.get("type") == "mcpToolCall" else item["type"]
            tools[item.get("id")] = {"name": name, "issued_ms": round((stamp - start) * 1000, 3),
                                     "input_chars": len(json.dumps(item.get("arguments", item.get("command"))))}
        elif method == "item/completed" and item.get("id") in tools:
            entry = tools[item["id"]]
            entry["returned_ms"] = round((stamp - start) * 1000, 3)
            entry["round_trip_ms"] = round(entry["returned_ms"] - entry["issued_ms"], 3)
            entry["is_error"] = item.get("status") == "failed" or bool(item.get("error"))
        elif method == "thread/tokenUsage/updated":
            requests += 1
            last = (params.get("tokenUsage") or {}).get("last")
            if isinstance(last, dict):
                usage_last.append(last.get("inputTokens"))
        elif method == "account/rateLimits/updated":
            snapshot = params.get("rateLimits") or {}
            rate_limits.append({key: snapshot.get(key) for key in ("planType", "primary", "secondary") if key in snapshot})
    calls = list(tools.values())
    return {"init": None, "first_text_ms": first_text, "first_tool_call_ms": first_tool, "api_calls": requests,
            "prompt_tokens_per_request": usage_last, "tool_calls": calls, "tool_call_count": len(calls),
            "tool_names": [call["name"] for call in calls], "tool_errors": sum(bool(c.get("is_error")) for c in calls),
            "api_retries": [], "rate_limits": rate_limits[-1:],
            "tool_round_trip_ms_total": round(sum(call.get("round_trip_ms", 0) for call in calls), 3)}


def final_message(events):
    """The turn's last agent message, whole (an answer line is its last line)."""
    return str(next(((message["params"].get("item") or {}).get("text") for _, message in reversed(events)
                     if message.get("method") == "item/completed"
                     and ((message.get("params") or {}).get("item") or {}).get("type") == "agentMessage"), "") or "")


def result_summary(completed, events):
    """The turn's status, error and final agent message (Claude's ``result_summary`` fields where they exist)."""
    if not completed:
        return None
    turn = completed.get("turn") or {}
    reply = final_message(events)
    models = sorted({(message.get("params") or {}).get("model") for _, message in events
                     if message.get("method") == "model/rerouted"} - {None})
    return {"subtype": "success" if turn.get("status") == "completed" else turn.get("status"),
            "is_error": turn.get("status") == "failed", "error": turn.get("error"), "models": models,
            "reply": reply[:1500]}


def run_task(task, client, stream, usage, config, trial, scenario_output=None):
    record = {"side": "mcp", "runtime": "codex", "trial": trial, "task": task["id"], "kind": task["kind"],
              "source": task["source"], **bench_integrity.trial_stamp(task)}   # What it was sent and graded with.
    if config.get("after_failure") == "reset":
        steps = client.execute(grading.reset_code(task["id"], *trial_base.task_set(config))).get("steps")
        if steps:
            record["harness_reset"] = steps
    answer = answer_check.before(client, grading, task, trial_base.task_set(config), record)
    snapshot = client.execute(grading.snapshot_code())
    if task["requires_object"] and not snapshot["eval_object_present"]:
        answer_check.undo(client, grading, answer)
        record.update(status="skipped", reason="the object from an earlier task is missing")
        return record
    images = scenario.attach(task, record)
    client.settle()
    cold = stream.thread_id is None
    sent = time.monotonic()
    if config.get("new_thread_per_task") and not cold:   # --returning-user: a new conversation per task.
        stream.new_thread()
        usage.thread_started(stream.thread_id)
    if cold:
        stream.start()   # The runtime launch is part of the first message, as on the Angelo side.
        record["mcp_server_wait"] = {"status": stream.mcp_status, "ms": stream.mcp_ready_ms}
        usage.thread_started(stream.thread_id)
    usage.begin_turn(stream.thread_id)
    stream.send(task["prompt"], images)
    record["sent_at_s"] = round(sent - stream.spawned, 4)
    events, completed, problem = stream.collect(sent + config["task_timeout"], task["id"])
    status = None
    if problem == "deadline":
        stream.interrupt()
        more, completed, problem = stream.collect(time.monotonic() + trial_base.STOP_GRACE_S, task["id"])
        events += more
        status = "timeout"
    for _, message in events:
        if message.get("method") == "thread/tokenUsage/updated":
            usage.observe(stream.thread_id, (message.get("params") or {}).get("tokenUsage"))
    finished = next((stamp for stamp, message in reversed(events) if message.get("method") == "turn/completed"),
                    time.monotonic())
    record.update(runtime_start="cold" if cold else "warm", wall_ms=round((finished - sent) * 1000, 3),
                  **analyze_codex_events(events, sent))
    record["result"] = result_summary(completed, events)
    if completed:   # Whole: the grader reads which file it names as final (final_render); ``result`` keeps 1,500.
        record["final_reply"] = final_message(events)
    record["provider_usage"] = usage.finish_turn(round((finished - sent) * 1000))
    record["request_parameters"] = mcp_request_parameters.codex(config, events)   # Effort, summaries, per request.
    first = next((((m.get("params") or {}).get("tokenUsage") or {}).get("last") for _, m in events
                  if m.get("method") == "thread/tokenUsage/updated"), None)
    record["thread_id"] = stream.thread_id
    record["first_request"] = first and {"input": first.get("inputTokens"), "cached": first.get("cachedInputTokens"),
                                         "output": first.get("outputTokens")}
    try:
        if task.get("scenario_checks"):
            scenario.grade(client, task, config, record, scenario_output)
        else:
            record.update(client.execute(grading.grade_code(task["id"], snapshot["digest"],
                                                            *trial_base.task_set(config))))
    except (OSError, RuntimeError, ValueError) as error:
        record.update(host_check={"error": str(error)[:2000]}, host_check_passed=False)
    answer_check.after(client, grading, task, record, answer, answer_check.codex_rows(events),
                       stopped=status == "timeout")
    succeeded = bool(completed) and (completed.get("turn") or {}).get("status") == "completed"
    record["status"] = status or ("completed" if succeeded else "failed")
    limit = limits.codex_limit([message for _, message in events], completed)
    if limit:
        record.update(status="invalid", invalid=limits.INVALID, abort=limits.STOPPED_MESSAGE % limit)
        return record
    if problem == "exited":
        record["abort"] = "Codex exited; no further billed messages were sent"
    elif stream.unexpected_requests:
        record["abort"] = "Codex asked for input (%s); stopped the trial" % ", ".join(sorted(set(stream.unexpected_requests)))
    elif cold and not record["tool_call_count"] and record["status"] == "failed":
        record["abort"] = "Codex failed its first turn without a tool call; stopped the trial"
    return record


def run_trial(config, trial, folder, fake_base_url=None, tasks=None):
    """One fresh Blender + Codex pair over the head-to-head tasks (``tasks``: a dry run's subset)."""
    folder.mkdir(parents=True)
    for name in ("tmp", "codex-sqlite"):
        (folder / name).mkdir()
    from angelo_agent.provider_usage import CodexThreadUsage
    if tasks is None:
        tasks = [task for task in live.tasks_for(*trial_base.task_set(config)) if task["kind"] != "preview"]
    port = trial_base.free_port()
    version = eval_gate.set_version(trial_base.task_set(config)[0], live.tasks_for(*trial_base.task_set(config)))
    output = {"side": "mcp", "runtime": "codex", "trial": trial, "port": port, "tasks": [], "success": False,
              "gate_version": version, "test_code": bench_integrity.test_code(),
              "load_average_start": (list(os.getloadavg()) if hasattr(os, "getloadavg") else None), "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    blender = stream = client = None
    try:   # An empty folder where no project context reaches Codex (bench_workdirs).
        cwd = workdirs.fresh(config["claude_cwd"], config.get("codex_home"), "codex")
        blender, output["blender"] = trial_base.launch_blender(config, folder, port)
        client = trial_base.AddonClient(port)
        output["mcp_server"] = trial_base.probe_mcp_server(
            trial_base.mcp_config(config, port)["mcpServers"][SERVER], trial_base.clean_environment(TMPDIR=folder / "tmp"),
            folder / "tmp", list_only=True)
        output["codex_command"] = codex_command(config, port, fake_base_url)
        stream = CodexStream(output["codex_command"], environment(config, folder, fake_base_url), cwd, folder, config)
        scenario.guard(stream, config, folder, output)
        usage = CodexThreadUsage()
        if scenario.is_scenario(config):
            scenario.begin(client, config, output)
        for task in tasks:
            record = run_task(task, client, stream, usage, config, trial, output)
            record.update({"gate_version": version} if version else {})
            record["grading_contract"] = eval_grading.CONTRACT
            output["tasks"].append(record)
            output["account"] = stream.account
            (folder / "trial.json").write_text(json.dumps(output, indent=2, default=str))
            keys = ("trial", "task", "status", "host_check_passed", "wall_ms", "api_calls", "tool_call_count")
            print("BENCH_MCP_TASK", json.dumps({key: record.get(key) for key in keys}), flush=True)
            if record.get("abort"):
                output["aborted"] = record["abort"]
                if record.get("invalid"):
                    output["invalid"] = record["invalid"]
                break
        output["success"] = not output.get("aborted") and all(t["status"] != "error" for t in output["tasks"])
        if scenario.is_scenario(config) and output["tasks"]:
            scenario.finish(client, folder, config, output)
    except Exception:
        output["error"] = traceback.format_exc()
    finally:
        if stream is not None:
            output["codex_stderr_tail"] = stream.rpc.stderr_tail[-8:] if stream.rpc else []
            stream.close()
        if blender is not None:
            trial_base.quit_blender(blender, client) if client else trial_base.stop_process(blender, grace=1.)
    output["load_average_end"] = (list(os.getloadavg()) if hasattr(os, "getloadavg") else None)
    (folder / "trial.json").write_text(json.dumps(output, indent=2, default=str))
    return output


def preflight(config):
    """Refusals for the Codex MCP side, before any process or billed message."""
    problems = []
    if not config["codex"]:
        problems.append("--codex is required here: it has no default on this platform")
    elif not (Path(config["codex"]).is_file() and os.access(config["codex"], os.X_OK)):
        problems.append("--codex: no executable at %s" % config["codex"])
    home = Path(config.get("codex_home") or "/nonexistent").resolve()
    if home == (Path.home() / ".codex").resolve():
        return problems + ["--codex-home must be a dedicated profile, never ~/.codex"]  # Nothing in it is read.
    if not (home / "auth.json").is_file():   # Existence only: the file is never read.
        problems.append("--codex-home is not signed in (run: CODEX_HOME=%s codex login)" % home)
    try:
        import tomllib
        declared = tomllib.loads((home / "config.toml").read_text()).get("mcp_servers") if (home / "config.toml").is_file() else None
    except (OSError, ValueError):
        declared = {"unreadable config.toml": {}}
    if declared:
        problems.append("--codex-home declares MCP servers of its own (%s)" % ", ".join(sorted(declared)))
    return problems


def dry_run(config, folder):
    """No model and no billing: the real Codex, MCP server, add-on and grading against a scripted model.

    A local Responses endpoint answers the read-only question, then builds the
    cube task's expected scene through ``tools.mcp__blender__execute_blender_code``
    from code mode's ``exec``, as a GPT-6 model would. A fresh, signed-out
    ``CODEX_HOME`` is used; nothing reaches OpenAI.
    """
    from codex_prompt_probe import FakeResponses
    folder.mkdir(parents=True)
    (folder / "requests").mkdir()
    (folder / "codex-home").mkdir()
    code = grading.staged_expected_scene_code("cube")
    build = ("const r = await tools.mcp__%s__execute_blender_code({code: %s}); text(JSON.stringify(r).slice(0, 300));"
             % (SERVER, json.dumps(code)))
    answer = "Two mesh objects.\nMESHES: %s, Cube" % eval_gate.PROBE_MESH   # The question's own answer line.
    fake = FakeResponses(folder / "requests", [("message", answer), ("exec", build), ("message", "")])
    tasks = [task for task in live.build_tasks() if task["id"] in ("read_only_question", "cube_creation")]
    output = run_trial(dict(config, codex_home=str(folder / "codex-home")), 1, folder / "trial",
                       fake_base_url="http://127.0.0.1:%d/v1" % fake.port, tasks=tasks)
    records = {record["task"]: record for record in output["tasks"]}
    output["checks"] = {task: bool(record.get("host_check_passed")) for task, record in records.items()}
    output["success"] = (output["checks"] == {"read_only_question": True, "cube_creation": True}
                         and (records["cube_creation"].get("tool_names") or [None])[0]
                         == "mcp__%s__execute_blender_code" % SERVER
                         and not output.get("aborted") and not output.get("error")
                         and "execute_blender_code" in output.get("mcp_server", {}).get("tools", []))
    (folder / "dry-run.json").write_text(json.dumps(output, indent=2, default=str))
    return output
