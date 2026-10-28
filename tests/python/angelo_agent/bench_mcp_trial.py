# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One BILLED Blender MCP + Claude Code trial for ``bench_mcp_vs_angelo.py``.

Per trial: a fresh stock-Blender GUI process (``--factory-startup``, default-cube
scene, isolated ``BLENDER_USER_*`` and ``TMPDIR``) running a private COPY of
Blender Lab's MCP add-on on a free port in 39000-39999 (never 9876, the add-on's
default, left to an interactive pair), and a fresh headless Claude Code session
(``-p --input-format stream-json``) whose only MCP server is Blender Lab's
installed ``blender-mcp`` pointed at that port. The tasks run as one continuing
chat, exactly as the Angelo side's chat does.

Timing: wall time from writing a prompt to Claude Code's stdin until its
``result`` line arrives. The first prompt also starts the runtime (process spawn
is the start), like Angelo's cold first message, whose total includes its
runtime launch. Before every prompt the driver waits until the add-on has been
idle for ``SETTLE_S`` so each prompt meets the add-on's default idle polling,
as after a human pause, and the driver's own grading requests cannot speed up
the next turn.

Correctness is graded after each turn by the driver over the add-on socket with
the Angelo harness's own predicates (``bench_mcp_grading``), never by the model.
Nothing is retried. Raw Claude Code output is kept for later analysis.
"""

import json
import os
import queue
import random
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import bench_gpu
import bench_integrity
import bench_mcp_answers as answer_check
import bench_mcp_grading as grading
import bench_mcp_report as report
import bench_mcp_scenario as scenario
import bench_workdirs as workdirs
import eval_diagnostics
import eval_gate
import eval_grading
import eval_live_tasks as live
import gui_launch
import eval_provider_limits as limits
import mcp_request_parameters
import oneshot_metrics

HERE = Path(__file__).resolve().parent
STARTUP_SCRIPT = HERE / "bench_mcp_blender_startup.py"
MAINTAINER_PORT = 9876
PORT_RANGE = (39000, 40000)
SETTLE_S = 6.0      # Longer than the add-on's 5 s idle delay.
STOP_GRACE_S = 30.
SERVER_NAME = "blender"
ALLOWED_TOOLS = "mcp__" + SERVER_NAME   # Every tool of that one MCP server, and nothing else.
KEEP_ENV = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE", "__CF_USER_TEXT_ENCODING",
            # Windows: without these a child Python, Node or Blender cannot start or find its profile folders.
            "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "USERPROFILE", "USERNAME",
            "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "HOMEDRIVE", "HOMEPATH")
MCP_PROBE_STDERR = "mcp-server-stderr.log"   # probe_mcp_server: the server's own stderr, beside the probe.
WINDOW = ("--window-geometry", "60", "60", "1400", "900")   # The Angelo harness's geometry.


def clean_environment(**extra):
    """The minimal inherited environment both sides are started with.

    A benchmark launched from an agent session inherits routing, account and
    session variables (``ANTHROPIC_BASE_URL``, ``CLAUDE_CODE_*``...). They could
    route a run through another account or change Claude Code's behaviour, so
    only basic variables pass. ``DISABLE_AUTOUPDATER`` pins the CLI for a run.
    """
    environment = {key: os.environ[key] for key in KEEP_ENV if key in os.environ}
    environment["DISABLE_AUTOUPDATER"] = "1"
    # Where the run's scenario references are (scenario_specs.REFERENCES_ENV): the test's own data, the same for
    # both sides. Dropped, a moved root made every child refuse before its first message (Windows dev run, 22:28).
    environment.update({key: os.environ[key] for key in ("ANGELO_SCENARIO_REFERENCES",) if key in os.environ})
    # A non-secret QA owner lets the outer slot stop its own orphaned children.
    # It never selects an account or changes a request; malformed markers drop.
    owner = os.environ.get("ANGELO_QA_PROCESS_OWNER", "")
    if len(owner) == 32 and all(char in "0123456789abcdef" for char in owner):
        environment["ANGELO_QA_PROCESS_OWNER"] = owner
    # Measurement runs keep the pinned model: no automatic switch after a
    # refusal. Both sides stay on the requested model (the Angelo side sets the
    # same through its controller, which drops inherited copies).
    environment["CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK"] = "1"
    # The GPU test VMs' Cycles preference (bench_gpu): decided once by the run, the same for every child of it.
    environment.update(bench_gpu.environment())
    environment.update({key: str(value) for key, value in extra.items()})
    return environment


def free_port():
    candidates = list(range(*PORT_RANGE))
    random.shuffle(candidates)
    for port in candidates[:200]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    raise RuntimeError("No free benchmark port in %d-%d" % (PORT_RANGE[0], PORT_RANGE[1] - 1))


class AddonClient:
    """The add-on's own request format, as ``blmcp.tools_helpers.connection.send_code`` sends it."""

    def __init__(self, port):
        if port == MAINTAINER_PORT or not PORT_RANGE[0] <= port < PORT_RANGE[1]:
            raise ValueError("Refusing port %d: only the trial's own 39000-39999 port is used" % port)
        self.port, self.last_activity = port, 0.

    def execute(self, code, timeout=120.):
        request = (json.dumps({"type": "execute", "code": code, "strict_json": True}) + "\0").encode("utf-8")
        buffer = bytearray()
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=timeout) as connection:
                connection.sendall(request)
                while b"\0" not in buffer:
                    chunk = connection.recv(65536)
                    if not chunk:
                        break
                    buffer.extend(chunk)
        finally:
            self.last_activity = time.monotonic()
        response = json.loads(bytes(buffer.partition(b"\0")[0]) or b"{}")
        if response.get("status") != "ok":
            raise RuntimeError("Add-on request failed: %s" % str(response.get("message") or response)[:2000])
        return response["result"]

    def settle(self):
        """Wait until the add-on is back on its idle polling interval."""
        remaining = self.last_activity + SETTLE_S - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)


def launch_blender(config, folder, port):
    user = folder / "blender-user"
    shutil.copytree(config["mcp_addon_dir"], user / "extensions/user_default/mcp",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("config", "scripts", "datafiles"):
        (user / name).mkdir(parents=True, exist_ok=True)
    ready = folder / "blender-ready.json"
    environment = clean_environment(
        BLENDER_USER_RESOURCES=user, BLENDER_USER_CONFIG=user / "config", BLENDER_USER_SCRIPTS=user / "scripts",
        BLENDER_USER_EXTENSIONS=user / "extensions", BLENDER_USER_DATAFILES=user / "datafiles",
        TMPDIR=folder / "tmp", ANGELO_BENCH_MCP_PORT=port, ANGELO_BENCH_MCP_READY=ready)
    command = [config["stock_blender"], "--factory-startup", "--online-mode", "--disable-autoexec",
               *gui_launch.window_flags(config.get("window_state", "behind"),
                                        input_isolation=config.get("input_isolation", "on") == "on"),
               *WINDOW, "--python-exit-code", "1", "--python", str(STARTUP_SCRIPT)]
    log = (folder / "blender.log").open("w")
    process = subprocess.Popen(command, cwd=folder, env=environment, stdout=log, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 120
    while not ready.is_file():
        if process.poll() is not None or time.monotonic() > deadline:
            stop_process(process)
            raise RuntimeError("Stock Blender did not start the MCP add-on server; see %s" % log.name)
        time.sleep(.2)
    info = json.loads(ready.read_text())
    if not info.get("ok"):
        stop_process(process)
        raise RuntimeError("MCP add-on did not start: %s" % info)
    return process, info


def stop_process(process, grace=30.):
    if process is None or process.poll() is not None:
        return
    try:
        process.wait(grace)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.kill()


def quit_blender(process, client):
    # The quit runs from a timer, after the add-on's exec (and its weak sandbox) returned.
    try:
        client.execute("import bpy\nbpy.app.timers.register(lambda: bpy.ops.wm.quit_blender() and None, "
                       "first_interval=.2)\nresult = {}", timeout=15)
    except (OSError, RuntimeError, ValueError):
        pass
    stop_process(process)


def server_executable(server_dir, platform=None):
    """The installed server's entry point in its own virtualenv (``Scripts\\blender-mcp.exe`` on Windows)."""
    windows = (platform or sys.platform) == "win32"
    return str(Path(server_dir) / (".venv/Scripts/blender-mcp.exe" if windows else ".venv/bin/blender-mcp"))


def mcp_config(config, port):
    """Blender Lab's installed server, run from its own virtualenv without ``uv`` syncing it."""
    return {"mcpServers": {SERVER_NAME: {
        "type": "stdio", "command": server_executable(config["mcp_server_dir"]), "args": [],
        "env": {"BLENDER_MCP_HOST": "127.0.0.1", "BLENDER_MCP_PORT": str(port),
                "BLENDER_PATH": config["stock_blender"], "PYTHONDONTWRITEBYTECODE": "1"}}}}


def claude_command(config, mcp_config_path, server_tools=()):
    """Claude Code's default system prompt and built-in tools; only the MCP server's tools are pre-allowed.

    The allow rule names the server (every tool it offers) plus, when known,
    each of its tools explicitly, so no MCP call can stop at a prompt.
    ``--permission-prompts none`` denies (and reports) anything else that would
    prompt, since nobody answers prompts in a headless run. Session persistence
    is off so the account profile gains no transcript; stdout is kept instead.
    """
    allowed = ",".join([ALLOWED_TOOLS] + ["%s__%s" % (ALLOWED_TOOLS, name) for name in server_tools])
    return [config["claude"], "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--include-partial-messages", "--model", config["model"], "--effort", config["effort"],
            "--settings", json.dumps({"fastMode": False}), "--mcp-config", str(mcp_config_path),
            "--strict-mcp-config", "--allowedTools", allowed, "--permission-prompts", "none",
            "--no-session-persistence", *eval_diagnostics.claude_arguments(config)]


class ClaudeStream:
    """One headless Claude Code process; every stdout line is timestamped on arrival."""

    def __init__(self, command, environment, cwd, folder):
        self.command, self.environment, self.cwd = command, environment, cwd
        self.raw = (folder / "claude-stream.jsonl").open("w", encoding="utf-8")
        self.stderr = (folder / "claude-stderr.log").open("w", encoding="utf-8")
        self.lines, self.process, self.spawned, self.requests = queue.Queue(), None, None, 0

    def start(self):
        self.spawned = time.monotonic()
        self.process = subprocess.Popen(self.command, cwd=self.cwd, env=self.environment, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=self.stderr, text=True, encoding="utf-8",
                                        bufsize=1)
        threading.Thread(target=self._read, daemon=True, name="BenchClaudeStdout").start()

    def _read(self):
        for line in self.process.stdout:
            stamp = time.monotonic()
            try:
                message = json.loads(line)
            except ValueError:
                message = {"type": "unparsed", "text": line[:4000]}
            self.lines.put((stamp, message))
        self.lines.put((time.monotonic(), None))

    def write(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def send(self, text, images=()):
        """One user turn; scenario turns attach their reference images as base64 image blocks."""
        self.write({"type": "user", "session_id": "", "parent_tool_use_id": None,
                    "message": {"role": "user", "content": scenario.claude_content(text, images)}})

    def interrupt(self):
        self.requests += 1
        self.write({"type": "control_request", "request_id": "bench-interrupt-%d" % self.requests,
                    "request": {"subtype": "interrupt"}})

    def collect(self, deadline, task):
        """This turn's messages up to its ``result``; returns (events, result, problem)."""
        events = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return events, None, "deadline"
            try:
                stamp, message = self.lines.get(timeout=min(remaining, 1.))
            except queue.Empty:
                continue
            if message is None:
                return events, None, "exited"
            events.append((stamp, message))
            self.raw.write(json.dumps({"t_s": round(stamp - self.spawned, 4), "task": task, "message": message}) + "\n")
            if message.get("type") == "result":
                self.raw.flush()
                return events, message, None

    def close(self):
        if self.process is not None:
            try:
                self.process.stdin.close()
            except OSError:
                pass
            stop_process(self.process, grace=15.)
        self.raw.close()
        self.stderr.close()


def task_set(config):
    """``(name, count)`` of the configured task set; the six-task default when unset."""
    return config.get("task_set", "default"), config.get("long_tasks", live.LONG_SESSION_TASKS)


def run_task(task, client, stream, usage, config, trial, scenario_output=None):
    record = {"side": "mcp", "trial": trial, "task": task["id"], "kind": task["kind"], "source": task["source"],
              **bench_integrity.trial_stamp(task)}   # What it was sent and graded with (bench_integrity).
    if config.get("after_failure") == "reset":
        steps = client.execute(grading.reset_code(task["id"], *task_set(config))).get("steps")
        if steps:
            record["harness_reset"] = steps
    if task.get("fixture"):  # The harness makes it, never the model (live.prepare_fixture), as on the Angelo side.
        record["harness_fixture"] = client.execute(grading.fixture_code(task["id"], *task_set(config))).get("steps")
    answer = answer_check.before(client, grading, task, task_set(config), record)   # A question's truth; else None.
    snapshot = client.execute(grading.snapshot_code())
    if task["requires_object"] and not snapshot["eval_object_present"]:
        answer_check.undo(client, grading, answer)
        record.update(status="skipped", reason="the object from an earlier task is missing")
        return record
    images = scenario.attach(task, record)
    client.settle()
    cold = stream.process is None
    sent = time.monotonic()
    if cold:
        stream.start()   # The runtime launch is part of the first message, as on the Angelo side.
    stream.send(task["prompt"], images)
    record["sent_at_s"] = round(sent - stream.spawned, 4)   # Lets the raw stream be re-analysed offline.
    events, result, problem = stream.collect(sent + config["task_timeout"], task["id"])
    status = None
    if problem == "deadline":
        stream.interrupt()
        more, result, problem = stream.collect(time.monotonic() + STOP_GRACE_S, task["id"])
        events += more
        status = "timeout"
    finished = next((stamp for stamp, message in reversed(events) if message.get("type") == "result"), time.monotonic())
    record.update(runtime_start="cold" if cold else "warm", wall_ms=round((finished - sent) * 1000, 3),
                  **report.analyze_mcp_events(events, sent))
    record["result"] = report.result_summary(result)
    if result:   # Whole: the grader reads which file it names as final (final_render); ``result`` keeps 1,500.
        record["final_reply"] = str(result.get("result") or "")
    record["request_parameters"] = mcp_request_parameters.mcp(getattr(stream, "command", ()), events)   # Effort etc.
    # Process totals give the turn's tokens and cost; its responses' own usage, the cache writes per TTL.
    record["provider_usage"] = oneshot_metrics.with_write_split(usage.turn(result), events) if result else None
    try:
        if task.get("scenario_checks"):
            scenario.grade(client, task, config, record, scenario_output)
        else:
            record.update(client.execute(grading.grade_code(task["id"], snapshot["digest"], *task_set(config))))
    except (OSError, RuntimeError, ValueError) as error:
        record.update(host_check={"error": str(error)[:2000]}, host_check_passed=False)
    answer_check.after(client, grading, task, record, answer, answer_check.claude_rows(events, result),
                       stopped=status == "timeout")
    succeeded = bool(result) and result.get("subtype") == "success" and not result.get("is_error")
    record["status"] = status or ("completed" if succeeded else "failed")
    limit = limits.mcp_limit(events, result, record["provider_usage"])
    if limit:
        # The account's limit, not the task: an invalid trial, and no further billed messages.
        record.update(status="invalid", invalid=limits.INVALID, abort=limits.STOPPED_MESSAGE % limit)
        return record
    servers = {entry.get("name"): entry.get("status") for entry in (record.get("init") or {}).get("mcp_servers", [])}
    if problem == "exited":
        record["abort"] = "Claude Code exited; no further billed messages were sent"
    elif cold and servers.get(SERVER_NAME) in (None, "failed", "needs-auth"):
        record["abort"] = "The Blender MCP server was not available (%s); stopped the trial" % servers
    elif result and result.get("is_error") and not record.get("api_calls"):
        record["abort"] = "Claude Code returned an error before any model call; stopped the trial"
    return record


def run_trial(config, trial, folder):
    """One fresh Blender + Claude Code pair over the head-to-head tasks."""
    folder.mkdir(parents=True)
    (folder / "tmp").mkdir()
    from angelo_agent.provider_usage import ClaudeProcessUsage
    tasks = [task for task in live.tasks_for(*task_set(config)) if task["kind"] != "preview"]
    port, version = free_port(), eval_gate.set_version(task_set(config)[0], tasks)
    output = {"side": "mcp", "trial": trial, "port": port, "tasks": [], "success": False, "gate_version": version,
              "test_code": bench_integrity.test_code(),
              "load_average_start": (list(os.getloadavg()) if hasattr(os, "getloadavg") else None), "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    blender = stream = client = None
    try:   # The same empty folder every trial, where no project context reaches the client (bench_workdirs).
        cwd = workdirs.fresh(config["claude_cwd"], config.get("claude_config_dir"), "claude")
        blender, output["blender"] = launch_blender(config, folder, port)
        client = AddonClient(port)
        config_path = folder / "mcp-config.json"
        config_path.write_text(json.dumps(mcp_config(config, port), indent=2))
        # tools/list only: it never reaches Blender, so the scene and the add-on stay untouched.
        output["mcp_server"] = probe_mcp_server(mcp_config(config, port)["mcpServers"][SERVER_NAME],
                                                clean_environment(TMPDIR=folder / "tmp"), folder / "tmp",
                                                list_only=True)
        output["claude_command"] = scenario.client_command(
            claude_command(config, config_path, output["mcp_server"]["tools"]), config, folder)
        # ``fake_model_environment``: the unbilled dry run's local Messages endpoint (scenario_fake_models).
        stream = ClaudeStream(output["claude_command"], clean_environment(
            CLAUDE_CONFIG_DIR=config["claude_config_dir"], TMPDIR=folder / "tmp",
            **config.get("fake_model_environment", {})), cwd, folder)
        scenario.guard(stream, config, folder, output)
        usage = ClaudeProcessUsage()
        if scenario.is_scenario(config):
            scenario.begin(client, config, output)
        for task in tasks:
            record = run_task(task, client, stream, usage, config, trial, output)
            record.update({"gate_version": version} if version else {})
            record["grading_contract"] = eval_grading.CONTRACT
            output["tasks"].append(record)
            (folder / "trial.json").write_text(json.dumps(output, indent=2, default=str))
            keys = ("trial", "task", "status", "host_check_passed", "wall_ms", "api_calls", "tool_call_count")
            print("BENCH_MCP_TASK", json.dumps({key: record.get(key) for key in keys}), flush=True)
            if record.get("abort"):
                output["aborted"] = record["abort"]
                if record.get("invalid"):
                    output["invalid"] = record["invalid"]
                break
        output["success"] = not output.get("aborted") and all(t["status"] != "error" for t in output["tasks"])
        # Hygiene, the saved copy, --ask-why-on-miss's follow-up on the graded scene, then the Undo probe.
        scenario.end_of_trial(client, stream, usage, folder, config, output)
    except Exception:
        output["error"] = traceback.format_exc()
    finally:
        if stream is not None:
            stream.close()
        if blender is not None:
            quit_blender(blender, client) if client else stop_process(blender, grace=1.)
    output["load_average_end"] = (list(os.getloadavg()) if hasattr(os, "getloadavg") else None)
    (folder / "trial.json").write_text(json.dumps(output, indent=2, default=str))
    return output


def probe_mcp_server(server, environment, cwd, list_only=False):
    """Speak MCP over stdio to the installed server exactly as configured for Claude Code (no model).

    The server's stderr goes to ``MCP_PROBE_STDERR`` in ``cwd``: a server that ends before it answers is reported
    with its exit code and the end of that file (a Windows dry run once saw only an empty first line).
    """
    errors = Path(cwd) / MCP_PROBE_STDERR
    with errors.open("wb") as handle:   # The child keeps its own copy of the handle.
        process = subprocess.Popen([server["command"], *server["args"]], cwd=cwd,
                                   env={**environment, **server["env"]}, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=handle, text=True)

    def ended(method):
        try:
            code = process.wait(10)
        except subprocess.TimeoutExpired:
            code = "still running"
        tail = errors.read_bytes()[-1500:].decode("utf-8", "replace").strip() if errors.is_file() else ""
        return RuntimeError("The MCP server %s ended before answering %s (exit code %s); its stderr (%s): %s"
                            % (server["command"], method, code, errors, tail or "empty"))

    def call(identity, method, params=None):
        request = {"jsonrpc": "2.0", "id": identity, "method": method, "params": params or {}}
        try:
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()
        except OSError:
            raise ended(method) from None
        line = process.stdout.readline()
        if not line:
            raise ended(method)
        return json.loads(line)

    try:
        started = time.monotonic()
        info = call(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                      "clientInfo": {"name": "angelo-bench-dry-run", "version": "1"}})["result"]
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        tools = [tool["name"] for tool in call(2, "tools/list")["result"]["tools"]]
        if list_only:
            return {"server_info": info.get("serverInfo"), "initialize_ms": _elapsed(started), "tools": tools}
        summary = call(3, "tools/call", {"name": "get_objects_summary", "arguments": {}})["result"]
        return {"server_info": info.get("serverInfo"), "initialize_ms": _elapsed(started), "tools": tools,
                "objects_summary_ok": not summary.get("isError"), "objects_summary": str(summary)[:600]}
    finally:
        try:
            process.stdin.close()
        except OSError:   # A server that ended left the pipe closed; the reason is already being raised.
            pass
        stop_process(process, grace=10.)
        process.stdout.close()


def _elapsed(started):
    return round((time.monotonic() - started) * 1000, 3)


def answer_dry_run(client, task):
    """The opening question as a trial asks it, with scripted replies: truth computed, answer read, fixture undone."""
    outcomes, errors = {}, []
    for label, reply in (("right", "Two mesh objects.\nMESHES: %s, Cube" % eval_gate.PROBE_MESH),
                         ("factory_guess", "One mesh object.\nMESHES: Cube"),
                         ("refusal", "I cannot see the scene."), ("empty", "")):
        record = {}
        made = answer_check.before(client, grading, task, ("default", live.LONG_SESSION_TASKS), record)
        record.update(client.execute(grading.grade_code(task["id"], client.execute(grading.snapshot_code())["digest"])))
        answer_check.after(client, grading, task, record, made, [{"role": "assistant", "text": reply}])
        outcomes[label] = [record["host_check"]["answer"]["outcome"], record["host_check_passed"]]
        errors += record.get("harness_fixture_errors", [])
    outcomes.update(objects_after=client.execute(grading.snapshot_code())["objects"], fixture_errors=errors)
    return outcomes


def dry_run(config, folder):
    """No model and no billing: prove launch, isolation, the MCP server path and grading."""
    folder.mkdir(parents=True)
    (folder / "tmp").mkdir()
    port = free_port()
    output = {"side": "mcp-dry-run", "port": port, "checks": {}}
    blender, output["blender"] = launch_blender(config, folder, port)
    client = AddonClient(port)
    tasks = [task for task in live.build_tasks() if task["kind"] != "preview"]

    def grade_all(digest):
        return {task["id"]: client.execute(grading.grade_code(task["id"], digest))["host_check_passed"]
                for task in tasks}

    try:
        before = client.execute(grading.snapshot_code())
        output["objects_at_start"] = before["objects"]
        output["mcp_server"] = probe_mcp_server(mcp_config(config, port)["mcpServers"][SERVER_NAME],
                                                clean_environment(TMPDIR=folder / "tmp"), folder / "tmp")
        output["checks"]["default_scene"] = grade_all(before["digest"])
        output["answers"] = answer_dry_run(client, tasks[0])
        client.execute(grading.staged_expected_scene_code("cube"))
        output["checks"]["cube_stage"] = grade_all(before["digest"])
        client.execute(grading.staged_expected_scene_code("finish"))
        output["checks"]["finished_stage"] = grade_all(before["digest"])
    finally:
        quit_blender(blender, client)
    checks, expected = output["checks"], {
        "default_scene": {"read_only_question": True, "cube_creation": False, "transform": False,
                          "bevel_and_shade": False, "material": False},
        "cube_stage": {"read_only_question": False, "cube_creation": True, "transform": False,
                       "bevel_and_shade": False, "material": False},
        "finished_stage": {"read_only_question": False, "cube_creation": False, "transform": True,
                           "bevel_and_shade": True, "material": True}}
    answers = {"right": ["right", True], "factory_guess": ["wrong", False], "refusal": ["no_answer", False],
               "empty": ["empty", False], "objects_after": ["Camera", "Cube", "Light"], "fixture_errors": []}
    # The GPU test VMs: the ready file's device line names the expected GPU (bench_gpu).
    output["gpu_problem"] = bench_gpu.dry_run_problem(output["blender"].get("gpu_preference"))
    output["success"] = (checks == expected and output["objects_at_start"] == ["Camera", "Cube", "Light"]
                         and output["gpu_problem"] is None
                         and output["answers"] == answers
                         and "execute_blender_code" in output["mcp_server"].get("tools", [])
                         and output["mcp_server"].get("objects_summary_ok") is True)
    (folder / "dry-run.json").write_text(json.dumps(output, indent=2, default=str))
    return output
