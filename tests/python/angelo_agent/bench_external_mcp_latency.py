# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Per-call latency of Angelo's MCP server against stock Blender with Blender Lab's MCP add-on (no model).

Both sides are driven the same way: a plain MCP client speaks stdio JSON-RPC to
each side's own server as an AI app would start it, and times each
``tools/call`` from the request line written to the response line read. The
same operations run on both, in rounds whose order alternates, after untimed
warm-up calls:

============== ================================ ===================================================
Operation      Angelo                           Blender + MCP
============== ================================ ===================================================
scene          angelo_inspect_scene             get_objects_summary
object         angelo_scene_object (the cube)   get_object_detail_summary (Cube)
move           angelo_set_transform             execute_blender_code (set location)
add_cube       angelo_create_cube               execute_blender_code (primitive_cube_add + rename)
view           angelo_view (SOLID, 512)         get_screenshot_of_area_as_image (VIEW_3D)
============== ================================ ===================================================

Each side keeps its own defaults, as a person gets them: Angelo in a new
External conversation (Approve for me, so the typed edits run unasked; the
person's snapshot is taken before the first edit of a request and is reported
apart as ``first_edit``), Blender MCP with its add-on's own polling. ``idle``
times a scene read after ``--idle`` seconds without calls (Blender MCP's
add-on slows its polling once idle). The views differ in kind (Angelo draws an
offscreen view of the scene; the add-on captures the area) and are reported as
"a picture of the 3D view". Python scripts are not timed: Angelo has a person
review each one from an app, which a timing cannot stand in for.

Run on a quiet machine, both apps behind other windows::

    python3 bench_external_mcp_latency.py --angelo-binary <Angelo.app/Contents/MacOS/Angelo> \\
        --stock-blender <Blender> --mcp-addon-dir <frozen mcp_addon> --mcp-server-dir <frozen mcp_server> \\
        --out <folder> [--rounds 20] [--idle 10]
"""

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROUNDS, WARMUP, IDLE = 20, 3, 10.0
CUBE_LOCATION = (0.0, 0.0, 0.0)


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


def _text(result):
    return " ".join(block.get("text", "") for block in result.get("content", []) if block.get("type") == "text")


class AngeloSide:
    name = "angelo"

    def __init__(self, client):
        self.client, self.handle, self.count = client, None, 0

    def prepare(self):
        _ms, result, failed = self.client.call("angelo_scene_search", {"query": "Cube", "coverage": "PAGE", "limit": 5})
        match = re.search(r'"(o[1-9][0-9]*)"', _text(result))
        if failed or not match:
            raise RuntimeError("Angelo did not return the cube's handle: %s" % _text(result)[:400])
        self.handle = match.group(1)

    def operation(self, name, round_index):
        self.count += 1
        if name in {"scene", "idle"}:
            return "angelo_inspect_scene", {}
        if name == "object":
            return "angelo_scene_object", {"object": self.handle}
        if name == "move":
            return "angelo_set_transform", {"object": self.handle, "location": [round_index * 0.01, 0, 0]}
        if name == "add_cube":
            return "angelo_create_cube", {"name": "Bench%03d" % self.count, "size": 0.2,
                                          "location": [2.0, round_index * 0.3, 0.0]}
        if name == "view":
            return "angelo_view", {"views": ["ISO"]}
        raise KeyError(name)


class McpSide:
    name = "blender_mcp"

    def __init__(self, client):
        self.client, self.count = client, 0

    def prepare(self):
        pass

    def operation(self, name, round_index):
        self.count += 1
        if name in {"scene", "idle"}:
            return "get_objects_summary", {}
        if name == "object":
            return "get_object_detail_summary", {"name": "Cube"}
        if name == "move":
            return "execute_blender_code", {"code": "import bpy\nbpy.data.objects['Cube'].location = (%r, 0, 0)\n"
                                                    "result = list(bpy.data.objects['Cube'].location)" % (round_index * 0.01)}
        if name == "add_cube":
            return "execute_blender_code", {"code": "import bpy\nbpy.ops.mesh.primitive_cube_add(size=0.2, location=(2.0, %r, 0.0))\n"
                                                    "bpy.context.active_object.name = 'Bench%03d'\nresult = bpy.context.active_object.name"
                                                    % (round_index * 0.3, self.count)}
        if name == "view":
            return "get_screenshot_of_area_as_image", {"area_ui_type": "VIEW_3D"}
        raise KeyError(name)


OPERATIONS = ("scene", "object", "move", "add_cube", "view")


def summarize(samples):
    if not samples:
        return {}
    ordered = sorted(samples)
    p75 = ordered[min(len(ordered) - 1, int(round(0.75 * (len(ordered) - 1))))]
    return {"n": len(samples), "p50_ms": round(statistics.median(samples), 1), "p75_ms": round(p75, 1),
            "min_ms": round(ordered[0], 1), "max_ms": round(ordered[-1], 1)}


def run_rounds(sides, rounds, warmup, idle):
    timings = {side.name: {name: [] for name in OPERATIONS + ("idle", "first_edit")} for side in sides}
    errors = {side.name: [] for side in sides}
    for side in sides:
        side.prepare()
    for index in range(warmup):   # Untimed: both sides warm (caches, first snapshot, first offscreen draw).
        for side in sides:
            for name in OPERATIONS:
                tool, arguments = side.operation(name, index)
                elapsed, result, failed = side.client.call(tool, arguments)
                if name in {"move", "add_cube"} and index == 0:
                    timings[side.name]["first_edit"].append(elapsed)
                if failed:
                    errors[side.name].append({"warmup": index, "op": name, "text": _text(result)[:300]})
    for index in range(rounds):
        order = sides if index % 2 == 0 else list(reversed(sides))
        for name in OPERATIONS:
            for side in order:
                tool, arguments = side.operation(name, warmup + index)
                elapsed, result, failed = side.client.call(tool, arguments)
                if failed:
                    errors[side.name].append({"round": index, "op": name, "text": _text(result)[:300]})
                else:
                    timings[side.name][name].append(elapsed)
    for _ in range(3):
        time.sleep(idle)
        for side in sides:
            tool, arguments = side.operation("idle", 0)
            elapsed, result, failed = side.client.call(tool, arguments)
            if not failed:
                timings[side.name]["idle"].append(elapsed)
    return {side: {name: summarize(values) for name, values in ops.items()} for side, ops in timings.items()}, errors


def main():
    sys.path.insert(0, str(HERE))
    import bench_mcp_trial
    from external_app_env import isolated_environment
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--angelo-binary", required=True)
    parser.add_argument("--stock-blender", required=True)
    parser.add_argument("--mcp-addon-dir", required=True)
    parser.add_argument("--mcp-server-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--rounds", type=int, default=ROUNDS)
    parser.add_argument("--warmup", type=int, default=WARMUP)
    parser.add_argument("--idle", type=float, default=IDLE)
    parser.add_argument("--port", type=int, default=9890)
    args = parser.parse_args()
    root = Path(args.out).resolve()
    root.mkdir(parents=True, exist_ok=True)
    angelo_root = root / "angelo"
    angelo_root.mkdir(mode=0o700, exist_ok=True)
    env = isolated_environment(angelo_root)
    binary = Path(args.angelo_binary).resolve(strict=True)
    resources = binary.parents[1] / "Resources" / "5.2"
    python = resources / "python" / "bin" / "python3.13"
    sys.path.insert(0, str(resources / "scripts" / "addons_core"))
    from angelo_agent.external_clients import AppRegistry, DEFAULT_LEVELS
    AppRegistry(env["ANGELO_STATE_DIR"]).connect("other", name="bench", levels=DEFAULT_LEVELS)
    ready, stop = angelo_root / "ready", angelo_root / "stop"
    for path in (ready, stop):
        path.unlink(missing_ok=True)
    import gui_launch
    angelo = subprocess.Popen(
        [str(binary), "--factory-startup", "--disable-autoexec", *gui_launch.window_flags("behind"),
         "--window-geometry", "80", "80", "1280", "800", "--python", str(Path(__file__).resolve()),
         "--", "--native-phase", "gui", "--ready", str(ready), "--stop", str(stop)],
        env=env, stdout=(angelo_root / "angelo.log").open("w"), stderr=subprocess.STDOUT)
    blender = server = angelo_client = None
    folder = root / "blender-mcp"
    folder.mkdir(exist_ok=True)
    (folder / "tmp").mkdir(exist_ok=True)
    try:
        deadline = time.monotonic() + 120
        while not ready.is_file():
            if angelo.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("Angelo did not start its listener; see angelo/angelo.log")
            time.sleep(0.2)
        launcher = resources / "scripts" / "angelo" / "angelo_mcp.py"
        angelo_client = StdioClient([str(python), "-I", str(launcher), "--client", "bench", "--state-dir",
                                     env["ANGELO_STATE_DIR"]], env, angelo_root, angelo_root / "launcher.stderr")
        config = {"stock_blender": args.stock_blender, "mcp_addon_dir": args.mcp_addon_dir,
                  "mcp_server_dir": args.mcp_server_dir, "window_state": "behind"}
        blender, blender_info = bench_mcp_trial.launch_blender(config, folder, args.port)
        entry = bench_mcp_trial.mcp_config(config, args.port)["mcpServers"][bench_mcp_trial.SERVER_NAME]
        server = StdioClient([entry["command"], *entry["args"]], {**bench_mcp_trial.clean_environment(), **entry["env"]},
                             folder, folder / "server.stderr")
        report = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "rounds": args.rounds, "warmup": args.warmup,
                  "idle_s": args.idle, "angelo_binary": str(binary), "stock_blender": args.stock_blender,
                  "blender": blender_info, "angelo_server": angelo_client.start(), "mcp_server": server.start()}
        results, errors = run_rounds([AngeloSide(angelo_client), McpSide(server)], args.rounds, args.warmup, args.idle)
        report.update(results=results, errors=errors)
        (root / "latency.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        return 0 if not any(errors.values()) else 1
    finally:
        stop.write_text("1")
        for client in (angelo_client, server):
            if client is not None:
                client.close()
        if blender is not None:
            bench_mcp_trial.stop_process(blender)
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
READY, STOP = Path(_args[_args.index("--ready") + 1]), Path(_args[_args.index("--stop") + 1])


def _start():
    prefs = bpy.context.preferences.addons.get("angelo_agent")
    if prefs is None:
        return 0.2
    prefs.preferences.allow_local_cli = True
    from angelo_agent import external_host
    if not (Path(os.environ["ANGELO_STATE_DIR"]) / "cli" / "endpoint.json").is_file():
        return 0.2
    READY.write_text(external_host.status_text())
    return None


def _stop():
    if STOP.is_file():
        bpy.ops.wm.quit_blender()
        return None
    return 0.5


bpy.app.timers.register(_start, first_interval=1.0)
bpy.app.timers.register(_stop, first_interval=2.0, persistent=True)
