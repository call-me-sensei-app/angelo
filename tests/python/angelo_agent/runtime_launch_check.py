# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A runtime release through Angelo's own chat path, unbilled: Claude Code's launch check, and launch timings.

No credentials are read and nothing leaves the machine. Uses:

* **Launch check** (doc/angelo/ai_providers.rst, "Claude Code's range"): a
  release passes when, launched exactly as a chat launches it, it answers
  ``initialize``, sends no Messages request before the person's message, sends
  Angelo's plan schema and instructions in the first one, and ends the turn
  with the plan.
* **Timings** (doc/angelo/performance.rst, "Runtime prewarm"): a new chat's
  first message, ``cold`` (the send launches Claude Code) and ``prewarm`` (it
  started while the message was written, ``runtime_prewarm``), per executable,
  alternating executables and arms in every trial.
* **Codex** (``--codex``): a new team chat's connect (the app-server, its
  checks and ``thread/start``; no model request), ``cold`` and ``prewarm``, and
  the prewarmed app-server's resident memory. Its gateway route is a dead local
  port, so no request leaves the machine.

Each trial is a fresh state folder and the real ``Controller``,
``ClaudeProvider`` and ``runtime_prewarm`` on an API-key connection (a dummy
key) with its own Claude profile. The only change to the product's launch is
``ANTHROPIC_BASE_URL``, the mock; every other host goes to a dead local proxy.
The mock answers each Messages request at once: Angelo's plan (an empty one,
through ``StructuredOutput``) or, after a tool result, "Done.". Per trial:

* ``first_request_ms``: the send to the first Messages request at the mock;
* ``finished_ms``: the send to the end of the turn;
* ``launch_ms``: the launch on the request's path (the receipt's
  ``runtime_start_ms``), and for a prewarm the time from the first keystroke
  to a warm session (``prewarm_ready_ms``);
* ``requests_before_send``, ``requests``: Messages requests before and during
  the turn (with their models), and ``rss_mb``: the idle session's resident
  memory just before the send.

Usage::

    python3.13 runtime_launch_check.py OUT_DIR [--claude PATH ...] [--codex PATH ...]
        [--trials 5] [--typing 2.0] [--arms cold,prewarm] [--below-floor]

Run it on a quiet machine: the timings are wall time.
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent import claude_transport, providers, runtime_prewarm, team_runtime  # noqa: E402
from angelo_agent.chat_settings import normalize_models  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from scenario_fake_models import FakeMessages  # noqa: E402

MESSAGE = "Make a 2 m cube named Eval Cube."
MARKER = "You are Angelo"
PLAN = {"reply": "Done.", "operations": [], "verify": False}
MODELS = normalize_models([{"value": "opus", "displayName": "Opus", "isDefault": True,
                            "supportedEffortLevels": ["low", "medium", "high"]}], "claude")


class PlanMessages(FakeMessages):
    """Angelo's plan for a Messages request offering ``StructuredOutput``, "Done." otherwise; arrival times kept."""

    def __init__(self, out):
        super().__init__(out, "launch-check", [], model="claude-opus-5-5")
        self.arrivals = []

    def reply(self, raw):
        self.arrivals.append(time.monotonic())
        return super().reply(raw)

    def _decide(self, body):
        tools = {tool.get("name") for tool in body.get("tools") or []}
        last = (body.get("messages") or [{}])[-1].get("content")
        answered = isinstance(last, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in last)
        return ("tool", "StructuredOutput", PLAN) if "StructuredOutput" in tools and not answered else ("text", "Done.")


def _version(executable):
    output = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=30).stdout
    return next((word for word in output.split() if word[:1].isdigit()), "unknown")  # "2.1.301 (Claude Code)", "codex-cli 0.162.0"


def _rss_mb(process):
    try:
        output = subprocess.run(["ps", "-o", "rss=", "-p", str(process.pid)], capture_output=True, text=True,
                                timeout=10).stdout.strip()
        return round(int(output) / 1024, 1)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _settle(controller, seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        controller.pump_events()
        if not controller.busy and not (controller._worker and controller._worker.is_alive()):
            controller.pump_events()
            return True
        time.sleep(.002)
    return False


def _first_request(body):
    system = body.get("system") or []
    text = system if isinstance(system, str) else " ".join(b.get("text", "") for b in system if isinstance(b, dict))
    tools = {tool.get("name"): tool for tool in body.get("tools") or []}
    schema = (tools.get("StructuredOutput") or {}).get("input_schema") or {}
    return {"instructions": MARKER in text, "plan_schema": "operations" in (schema.get("properties") or {}),
            "tools": sorted(tools)}


def trial(out, executable, arm, typing):
    """One new chat's first message; the record of what happened."""
    mock = PlanMessages(out / "requests")
    connection = str(uuid.uuid4())
    original = providers.child_environment

    def child_environment(mode, api_key=""):
        env = original(mode, api_key)
        env["ANTHROPIC_BASE_URL"] = mock.url  # The only change to the product's launch.
        return env
    controller = Controller(str(out / "state"))
    record = {"arm": arm}
    try:
        with patch.object(providers, "child_environment", child_environment):
            controller.set_document(("launch-check", 1))
            settings = {"model": "", "claude_path": executable, "connection_id": connection,
                        "reuse_codex_login": False, "auth_directory": str(out / "state")}
            controller.choose_connection("claude_api", settings, "launch-check-not-a-secret", connection, MODELS, "opus")
            controller.set_options(effort="low")
            runtime_prewarm.observe(controller, "", [controller], time.monotonic())
            if arm == "prewarm":
                began = time.monotonic()
                ready = None
                while time.monotonic() - began < typing:  # A person writing the message, a character at a time.
                    written = MESSAGE[:max(1, int(len(MESSAGE) * (time.monotonic() - began) / typing))]
                    runtime_prewarm.observe(controller, written, [controller], time.monotonic())
                    provider = controller.provider
                    if ready is None and provider is not None and provider.has_warm_runtime():
                        ready = time.monotonic()
                    time.sleep(.1)
                record["prewarm_ready_ms"] = None if ready is None else round((ready - began) * 1000, 1)
                transport = getattr(controller.provider, "_transport", None)
                record["rss_mb"] = _rss_mb(transport.process) if transport is not None else None
            else:
                time.sleep(typing)
            record["requests_before_send"] = len(mock.arrivals)
            sent = time.monotonic()
            controller.send(MESSAGE, {"objects": []})
            record["finished"] = _settle(controller)
            record["finished_ms"] = round((time.monotonic() - sent) * 1000, 1)
            arrivals = [t for t in mock.arrivals if t >= sent]
            record["first_request_ms"] = round((arrivals[0] - sent) * 1000, 1) if arrivals else None
            receipt = controller._request_metrics
            record["launch_ms"] = receipt.phases.get("runtime_start_ms") if receipt else None
            record["warm"] = bool(receipt and receipt.warm)
            record["prewarmed"] = bool(receipt and receipt.counts.get("runtime_prewarmed"))
            bodies = [json.loads(raw) for raw in mock.bodies]
            record["requests"] = [{"model": body.get("model"), "tools": len(body.get("tools") or []),
                                   "max_tokens": body.get("max_tokens")} for body in bodies]
            record["first"] = _first_request(bodies[0]) if bodies else None
            errors = [m["text"][:300] for m in controller.state["messages"] if m.get("role") == "error"]
            record["errors"] = errors
            record["passed"] = bool(record["finished"] and not errors and record["requests_before_send"] == 0
                                    and record["first"] and record["first"]["instructions"]
                                    and record["first"]["plan_schema"]
                                    and any(m.get("role") == "assistant" for m in controller.state["messages"]))
    finally:
        controller.close()
        mock.close()
    return record


def codex_trial(out, executable, arm, typing):
    """One new team chat's connect on its own app-server; no model request (the route is a dead local port)."""
    connection = str(uuid.uuid4())
    controller = Controller(str(out / "state"))
    record = {"arm": arm}
    try:
        with patch.object(team_runtime, "base_url", lambda mode: "http://127.0.0.1:9" + "/v1/openai/v1"):
            controller.set_document(("launch-check", 1))
            controller.state.update(provider="codex_team", connection_id=connection)
            controller.settings = {"model": "", "codex_path": executable, "connection_id": connection,
                                   "reuse_codex_login": False, "auth_directory": str(out / "state")}
            controller.api_key = "launch-check-not-a-secret"
            runtime_prewarm.observe(controller, "", [controller], time.monotonic())
            if arm == "prewarm":
                began, ready = time.monotonic(), None
                while time.monotonic() - began < typing:
                    written = MESSAGE[:max(1, int(len(MESSAGE) * (time.monotonic() - began) / typing))]
                    runtime_prewarm.observe(controller, written, [controller], time.monotonic())
                    provider = controller.provider
                    if ready is None and provider is not None and provider._prewarmed is not None:
                        ready = time.monotonic()
                    time.sleep(.1)
                record["prewarm_ready_ms"] = None if ready is None else round((ready - began) * 1000, 1)
                prewarmed = getattr(controller.provider, "_prewarmed", None)
                record["rss_mb"] = _rss_mb(prewarmed[0].process) if prewarmed else None
            else:
                time.sleep(typing)
            adopted = runtime_prewarm.adopt(controller)
            controller._begin_metrics()
            started = time.monotonic()
            native = controller._connect_runtime(controller.runtime_context())
            record["connect_ms"] = round((time.monotonic() - started) * 1000, 1)
            record["prewarmed"] = adopted
            record["passed"] = bool(native) and (arm == "cold" or adopted)
    except Exception as error:  # noqa: BLE001 - recorded: a failed connect is a failed check.
        record.update(passed=False, error=str(error)[:300])
    finally:
        controller.close()
    return record


def _median(values):
    values = [v for v in values if isinstance(v, (int, float))]
    return round(statistics.median(values), 1) if values else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--claude", action="append", default=[], help="A Claude Code executable; repeat to compare")
    parser.add_argument("--codex", action="append", default=[], help="A Codex executable; repeat to compare")
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--typing", type=float, default=2.0, help="Seconds between the first keystroke and Send")
    parser.add_argument("--arms", default="cold,prewarm")
    parser.add_argument("--below-floor", action="store_true",
                        help="Time a Claude Code older than the floor too (a comparison, not a launch check)")
    args = parser.parse_args(argv)
    if args.below_floor:
        patch.object(claude_transport, "MINIMUM_VERSION", (0, 0, 0)).start()
    out = args.out.resolve()
    for name in ("home", "tmp"):
        (out / name).mkdir(parents=True, exist_ok=True)
    # Nothing leaves the machine: every host but the mock (127.0.0.1) goes to a dead proxy.
    os.environ.update({"HOME": str(out / "home"), "TMPDIR": str(out / "tmp"), "HTTPS_PROXY": "http://127.0.0.1:9",
                       "HTTP_PROXY": "http://127.0.0.1:9", "NO_PROXY": "127.0.0.1,localhost"})
    executables = ([("claude", path, "claude " + _version(path)) for path in args.claude]
                   + [("codex", path, "codex " + _version(path)) for path in args.codex])
    arms = [arm for arm in args.arms.split(",") if arm]
    records = []
    for index in range(args.trials):
        order = executables if index % 2 == 0 else list(reversed(executables))
        for runtime, path, version in order:
            for arm in (arms if index % 2 == 0 else list(reversed(arms))):
                folder = out / ("trial-%02d" % index) / version.replace(" ", "-") / arm
                run = trial if runtime == "claude" else codex_trial
                record = dict(run(folder, path, arm, args.typing), version=version, trial=index)
                records.append(record)
                print(json.dumps({key: record.get(key) for key in ("trial", "version", "arm", "passed", "first_request_ms",
                                                                   "finished_ms", "launch_ms", "connect_ms",
                                                                   "prewarm_ready_ms", "rss_mb", "error")}), flush=True)
    summary = {}
    for _, _, version in executables:
        for arm in arms:
            rows = [r for r in records if r["version"] == version and r["arm"] == arm]
            summary["%s %s" % (version, arm)] = {
                "trials": len(rows), "passed": sum(1 for r in rows if r["passed"]),
                **{key: _median(r.get(key) for r in rows)
                   for key in ("first_request_ms", "finished_ms", "launch_ms", "connect_ms", "prewarm_ready_ms", "rss_mb")},
                "requests_per_turn": sorted({len(r.get("requests") or ()) for r in rows})}
    (out / "records.json").write_text(json.dumps(records, indent=1))
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    return 0 if all(r["passed"] for r in records) else 1


if __name__ == "__main__":
    sys.exit(main())
