# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Latency of Angelo's MCP server as an external agent uses it: reading and observing Angelo's agent (no model).

A plain MCP client speaks stdio JSON-RPC to Angelo's launcher as an AI app would start it and times each
``tools/call`` from the request line written to the response line read, after untimed warm-up calls:

============== ==========================================================================================
Operation      What is timed
============== ==========================================================================================
read           ``angelo_read`` with no wait on an idle conversation (one socket round trip and main tick)
scene          ``angelo_scene``: the observation the agent's next turn carries
viewport       ``angelo_viewport``: the agent's ``angelo_view`` of the person's viewport, as an image
row            a row's emission in the app's External conversation to its receipt by a long-polling
               ``angelo_read`` (``wait_seconds``), while the conversation is busy
============== ==========================================================================================

``row`` needs a running turn and no model runs here, so the Angelo side holds the conversation busy with the CLI's
own host request (``open_external_read``: no provider, no scene change) and appends one system row when the client
asks for it, writing the wall-clock time it did so; the client subtracts it from the time the answer arrived (one
machine, one clock). The first message the client sends creates the conversation and is answered
``needs_connection`` (no connection here), which is part of the run.

Run on a quiet machine, Angelo behind other windows::

    python3 bench_external_mcp_latency.py --angelo-binary <Angelo.app/Contents/MacOS/Angelo> --out <folder> \\
        [--rounds 20]
"""

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROUNDS, WARMUP = 20, 3
OPERATIONS = ("read", "scene", "viewport", "row")


class StdioClient:
    """A minimal MCP client over one server process's stdio."""

    def __init__(self, command, env, cwd, stderr_path):
        self.stderr = open(stderr_path, "wb")
        self.process = subprocess.Popen(command, env=env, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=self.stderr, text=True, bufsize=1)
        self.next = 1

    def request(self, method, params=None):
        identity, self.next = self.next, self.next + 1
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": identity, "method": method,
                                             "params": params or {}}) + "\n")
        self.process.stdin.flush()
        while True:
            line = self.process.stdout.readline()
            if not line:
                raise RuntimeError("The MCP server ended before answering %s" % method)
            message = json.loads(line)
            if message.get("id") == identity:
                return message

    def start(self):
        started = time.perf_counter()
        info = self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                           "clientInfo": {"name": "angelo-latency-bench", "version": "1"}})
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        self.process.stdin.flush()
        tools = self.request("tools/list")["result"]["tools"]
        return {"server": info["result"].get("serverInfo"), "initialize_ms": (time.perf_counter() - started) * 1000,
                "tools": len(tools), "tools_bytes": len(json.dumps(tools))}

    def call(self, name, arguments):
        started = time.perf_counter()
        message = self.request("tools/call", {"name": name, "arguments": arguments})
        elapsed = (time.perf_counter() - started) * 1000
        result = message.get("result") or {}
        return elapsed, result, bool(result.get("isError") or message.get("error"))

    def close(self):
        try:
            self.process.stdin.close()
            self.process.wait(10)
        except (OSError, subprocess.TimeoutExpired):
            self.process.kill()
        self.stderr.close()


def summarize(samples):
    if not samples:
        return {}
    ordered = sorted(samples)
    p75 = ordered[min(len(ordered) - 1, int(round(0.75 * (len(ordered) - 1))))]
    return {"n": len(samples), "p50_ms": round(statistics.median(samples), 1), "p75_ms": round(p75, 1),
            "min_ms": round(ordered[0], 1), "max_ms": round(ordered[-1], 1)}


def timed_row(client, folder, index):
    """One row's emission to its receipt through a parked read: ms, or None when it did not arrive."""
    cursor = client.call("angelo_read", {})[1].get("structuredContent", {}).get("cursor")
    request = folder / ("emit-%d" % index)
    stamp = folder / ("emitted-%d" % index)
    stamp.unlink(missing_ok=True)
    client.process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 10 ** 6 + index, "method": "tools/call",
                                           "params": {"name": "angelo_read", "arguments": {
                                               "cursor": cursor, "wait_seconds": 10}}}) + "\n")
    client.process.stdin.flush()
    time.sleep(1.0)   # Parked, past MIN_WAKE: what is timed is the stream path, not the coalescing floor.
    request.write_text("1")
    while True:
        line = client.process.stdout.readline()
        if not line:
            raise RuntimeError("The MCP server ended during a row measurement")
        if json.loads(line).get("id") == 10 ** 6 + index:
            received = time.time()
            break
    deadline = time.monotonic() + 10
    while not stamp.is_file() and time.monotonic() < deadline:
        time.sleep(0.01)
    return (received - float(stamp.read_text())) * 1000 if stamp.is_file() else None


def run(client, folder, rounds, warmup):
    timings, errors = {name: [] for name in OPERATIONS}, []
    sent = client.call("angelo_send", {"message": "Hello from the latency bench"})[1]
    errors_seen = {"first_send": (sent.get("structuredContent") or {}).get("code")}
    (folder / "busy").write_text("1")   # The Angelo side holds the conversation busy for the row timings.
    time.sleep(0.5)
    calls = {"read": ("angelo_read", {}), "scene": ("angelo_scene", {}), "viewport": ("angelo_viewport", {})}
    for index in range(warmup + rounds):
        for name in OPERATIONS:
            if name == "row":
                elapsed = timed_row(client, folder, index)
                failed = elapsed is None
            else:
                elapsed, result, failed = client.call(*calls[name])
            if failed:
                errors.append({"round": index, "op": name})
            elif index >= warmup:
                timings[name].append(elapsed)
    return {name: summarize(values) for name, values in timings.items()}, errors, errors_seen


def main():
    sys.path.insert(0, str(HERE))
    from external_app_env import isolated_environment
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--angelo-binary", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--rounds", type=int, default=ROUNDS)
    parser.add_argument("--warmup", type=int, default=WARMUP)
    args = parser.parse_args()
    root = Path(args.out).resolve()
    root.mkdir(parents=True, exist_ok=True)
    angelo_root = root / "angelo"
    angelo_root.mkdir(mode=0o700, exist_ok=True)
    env = isolated_environment(angelo_root)
    binary = Path(args.angelo_binary).resolve(strict=True)
    import app_layout
    resources, python = app_layout.resources(binary), app_layout.python(binary)
    sys.path.insert(0, str(resources / "scripts" / "addons_core"))
    from angelo_agent.external_clients import AppRegistry
    AppRegistry(env["ANGELO_STATE_DIR"]).connect("other", name="bench")
    ready, stop = angelo_root / "ready", angelo_root / "stop"
    for path in (ready, stop, angelo_root / "busy"):
        path.unlink(missing_ok=True)
    import gui_launch
    angelo = subprocess.Popen(
        [str(binary), "--factory-startup", "--disable-autoexec", *gui_launch.window_flags("behind"),
         "--window-geometry", "80", "80", "1280", "800", "--python", str(Path(__file__).resolve()),
         "--", "--native-phase", "gui", "--root", str(angelo_root)],
        env=env, stdout=(angelo_root / "angelo.log").open("w"), stderr=subprocess.STDOUT)
    client = None
    try:
        deadline = time.monotonic() + 120
        while not ready.is_file():
            if angelo.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("Angelo did not start its listener; see angelo/angelo.log")
            time.sleep(0.2)
        launcher = resources / "scripts" / "angelo" / "angelo_mcp.py"
        client = StdioClient([str(python), "-I", str(launcher), "--client", "bench", "--state-dir",
                              env["ANGELO_STATE_DIR"]], env, angelo_root, angelo_root / "launcher.stderr")
        report = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "rounds": args.rounds, "warmup": args.warmup,
                  "angelo_binary": str(binary), "angelo_server": client.start()}
        results, errors, seen = run(client, angelo_root, args.rounds, args.warmup)
        report.update(results=results, errors=errors, **seen)
        (root / "latency.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        return 0 if not errors else 1
    finally:
        stop.write_text("1")
        if client is not None:
            client.close()
        try:
            angelo.wait(30)
        except subprocess.TimeoutExpired:
            angelo.kill()
        runtime = Path(env["ANGELO_CLI_RUNTIME_DIR"])
        if not runtime.is_relative_to(angelo_root):
            shutil.rmtree(runtime, ignore_errors=True)


if "--native-phase" not in sys.argv:
    if __name__ == "__main__":
        sys.exit(main())
    raise SystemExit

import addon_utils  # noqa: E402
import bpy  # noqa: E402

addon_utils.enable("angelo_agent", default_set=False, persistent=False)   # As the fixtures do under --factory-startup.
bpy.context.preferences.use_preferences_save = False

_args = sys.argv[sys.argv.index("--") + 1:]
ROOT = Path(_args[_args.index("--root") + 1])
_held = {"fence": None, "count": 0}


def _start():
    prefs = bpy.context.preferences.addons.get("angelo_agent")
    if prefs is None:
        return 0.2
    from angelo_agent import grant_guard  # The test acts as the person (grant_guard.setting).
    grant_guard.harness(setattr, prefs.preferences, "allow_local_cli", True)
    from angelo_agent import external_host
    if not (Path(os.environ["ANGELO_STATE_DIR"]) / "cli" / "endpoint.json").is_file():
        return 0.2
    (ROOT / "ready").write_text(external_host.status_text())
    return None


def _serve():
    """Hold the bench app's conversation busy once asked, and append a row whenever the client asks for one."""
    from angelo_agent import external_host
    host = external_host._host
    controller = host.conversation("bench", create=False) if host is not None else None
    if controller is not None and (ROOT / "busy").is_file() and _held["fence"] is None:
        _held["fence"] = controller.open_external_read()
    for request in sorted(ROOT.glob("emit-*")) if controller is not None else ():
        index = request.name.split("-", 1)[1]
        request.unlink()
        controller.emit("message", {"role": "system", "text": "bench row %s" % index})
        (ROOT / ("emitted-" + index)).write_text(repr(time.time()))
    if (ROOT / "stop").is_file():
        if _held["fence"] is not None:
            controller.close_external_read(_held["fence"])
        bpy.ops.wm.quit_blender()
        return None
    return 0.005


bpy.app.timers.register(_start, first_interval=1.0)
bpy.app.timers.register(_serve, first_interval=2.0, persistent=True)
