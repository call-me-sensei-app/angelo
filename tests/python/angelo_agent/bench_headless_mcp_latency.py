# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Latency of Angelo's headless MCP against Blender MCP's CLI tools on the same .blend files (no model, no window).

Both sides are driven the same way: a plain MCP client speaks stdio JSON-RPC to each side's own server, started as
an AI app would start it, and times each ``tools/call`` from the request line written to the response line read.
Neither side has a window open. Blender MCP's ``*_for_cli`` tools start ``blender --background <file>`` for every
call; Angelo starts one background child per file at ``angelo_open_file`` and keeps it.

=========== ============================================ =====================================================
Operation   Angelo (headless)                            Blender MCP (CLI tools)
=========== ============================================ =====================================================
open        angelo_open_file (cold: a new child, the     get_blendfile_summary_datablocks_for_cli (every call
            file loaded, its summary and object list)    starts Blender)
summary     angelo_blend_file                            get_blendfile_summary_datablocks_for_cli
objects     angelo_inspect_scene                         execute_blender_code_for_cli (names, types, places)
add_cube    angelo_create_cube (saved to its new file)   execute_blender_code_for_cli (add a cube, save a copy)
move        angelo_set_transform (saved)                 execute_blender_code_for_cli (move, save the copy)
view        angelo_view (ISO)                            execute_blender_code_for_cli (512 px Workbench render)
=========== ============================================ =====================================================

Edits accumulate on both sides: Angelo in its session's output file; Blender MCP's calls open the copy its previous
edit saved. The views differ in kind (Angelo draws the viewport offscreen and returns the image; the CLI renders a
picture to a file), so they are reported as "a picture of the scene". Python is not timed: headless Angelo runs none.

Every warm operation runs ``--rounds`` times after ``--warmup`` untimed rounds, alternating which side goes first;
``open`` runs ``--cold`` times per side (Angelo closes and opens again; every Blender MCP call is cold). Both sides
get the same private HOME (factory settings, no add-ons). Run on a quiet machine holding ``/tmp/angelo-billed.lock``::

    <Angelo python> bench_headless_mcp_latency.py --angelo-binary <Angelo.app/Contents/MacOS/Angelo> \\
        --stock-blender <Blender> --mcp-server-dir <frozen mcp_server> --out <folder ending in .noindex>
"""

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROUNDS, WARMUP, COLD = 20, 2, 5
OPERATIONS = ("summary", "objects", "add_cube", "move", "view")
SCENES = {
    "factory": "",
    "objects_500": ("for index in range(499):\n"
                    "    obj = bpy.data.objects.new('Obj%03d' % index, bpy.data.objects['Cube'].data)\n"
                    "    obj.location = (index % 25, index // 25, 0)\n"
                    "    bpy.context.scene.collection.objects.link(obj)\n"),
}
FIXTURE = '''
import bpy, sys
bpy.ops.wm.read_factory_settings(use_empty=False)
{extra}
bpy.ops.wm.save_as_mainfile(filepath=sys.argv[sys.argv.index("--") + 1])
'''
OBJECTS_CODE = ("import bpy\nresult = {'objects': [{'name': o.name, 'type': o.type, 'location': list(o.location)} "
                "for o in list(bpy.context.scene.objects)[:200]], 'count': len(bpy.context.scene.objects)}")
ADD_CODE = ("import bpy\nbpy.ops.mesh.primitive_cube_add(size=0.2, location=(2.0, {y!r}, 0.0))\n"
            "bpy.context.active_object.name = 'Bench{n:03d}'\n"
            "bpy.ops.wm.save_as_mainfile(filepath={out!r}, copy=True)\nresult = {{'saved': {out!r}}}")
MOVE_CODE = ("import bpy\nbpy.data.objects['Cube'].location = ({x!r}, 0, 0)\n"
             "bpy.ops.wm.save_as_mainfile(filepath={out!r}, copy=True)\nresult = {{'saved': {out!r}}}")
VIEW_CODE = ("import bpy\nscene = bpy.context.scene\nscene.render.engine = 'BLENDER_WORKBENCH'\n"
             "scene.render.resolution_x = scene.render.resolution_y = 512\nscene.render.resolution_percentage = 100\n"
             "scene.render.filepath = {png!r}\nbpy.ops.render.render(write_still=True)\nresult = {{'image': {png!r}}}")


class StdioClient:
    """A minimal MCP client over one server process's stdio (bench_external_mcp_latency's, which cannot be imported:
    that module exits unless it runs inside Angelo or as a script)."""

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
        self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "angelo-headless-bench", "version": "1"}})
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        self.process.stdin.flush()
        return {"initialize_ms": round((time.perf_counter() - started) * 1000, 1)}

    def call(self, name, arguments):
        started = time.perf_counter()
        message = self.request("tools/call", {"name": name, "arguments": arguments})
        elapsed = (time.perf_counter() - started) * 1000
        result = message.get("result") or {}
        return elapsed, result, bool(result.get("isError") or message.get("error"))

    def close(self):
        try:
            self.process.stdin.close()
            self.process.wait(30)
        except (OSError, subprocess.TimeoutExpired):
            self.process.kill()
        self.stderr.close()


def summarize(samples):
    if not samples:
        return {"n": 0}
    ordered = sorted(samples)
    p75 = ordered[min(len(ordered) - 1, int(round(0.75 * (len(ordered) - 1))))]
    return {"n": len(samples), "p50_ms": round(statistics.median(samples), 1), "p75_ms": round(p75, 1),
            "min_ms": round(ordered[0], 1), "max_ms": round(ordered[-1], 1)}


class AngeloSide:
    name = "angelo_headless"

    def __init__(self, client, source):
        self.client, self.source, self.count = client, source, 0

    def open(self):
        self.client.call("angelo_close_file", {})
        return self.client.call("angelo_open_file", {"file": str(self.source)})

    def operation(self, name, index):
        self.count += 1
        if name == "summary":
            return "angelo_blend_file", {}
        if name == "objects":
            return "angelo_inspect_scene", {}
        if name == "add_cube":
            return "angelo_create_cube", {"name": "Bench%03d" % self.count, "size": 0.2,
                                          "location": [2.0, index * 0.3, 0.0]}
        if name == "move":
            return "angelo_set_transform", {"object": "Cube", "location": [index * 0.01, 0, 0]}
        if name == "view":
            return "angelo_view", {"views": ["ISO"]}
        raise KeyError(name)


class McpSide:
    name = "blender_mcp_cli"

    def __init__(self, client, source, folder):
        self.client, self.source, self.folder, self.count = client, source, folder, 0
        self.output = source.with_name(source.stem + "_mcp_out.blend")
        self.current = source

    def open(self):
        return self.client.call("get_blendfile_summary_datablocks_for_cli", {"blend_file": str(self.source)})

    def operation(self, name, index):
        self.count += 1
        if name == "summary":
            return "get_blendfile_summary_datablocks_for_cli", {"blend_file": str(self.current)}
        if name == "objects":
            return "execute_blender_code_for_cli", {"blend_file": str(self.current), "code": OBJECTS_CODE}
        if name in {"add_cube", "move"}:
            code = (ADD_CODE.format(y=index * 0.3, n=self.count, out=str(self.output)) if name == "add_cube"
                    else MOVE_CODE.format(x=index * 0.01, out=str(self.output)))
            arguments = {"blend_file": str(self.current), "code": code}
            self.current = self.output     # The next call opens the copy this one saves.
            return "execute_blender_code_for_cli", arguments
        if name == "view":
            png = str(self.folder / ("view-%03d.png" % self.count))
            return "execute_blender_code_for_cli", {"blend_file": str(self.current), "code": VIEW_CODE.format(png=png)}
        raise KeyError(name)


def run(sides, rounds, warmup, cold):
    timings = {side.name: {name: [] for name in ("open",) + OPERATIONS} for side in sides}
    errors = {side.name: [] for side in sides}
    for index in range(cold):
        for side in (sides if index % 2 == 0 else list(reversed(sides))):
            elapsed, result, failed = side.open()
            (errors[side.name].append({"cold": index, "text": _text(result)[:300]}) if failed
             else timings[side.name]["open"].append(elapsed))
    for index in range(warmup + rounds):
        order = sides if index % 2 == 0 else list(reversed(sides))
        for name in OPERATIONS:
            for side in order:
                tool, arguments = side.operation(name, index)
                elapsed, result, failed = side.client.call(tool, arguments)
                if failed:
                    errors[side.name].append({"round": index, "op": name, "text": _text(result)[:300]})
                elif index >= warmup:
                    timings[side.name][name].append(elapsed)
    return {side: {name: summarize(values) for name, values in ops.items()} for side, ops in timings.items()}, errors


def _text(result):
    return " ".join(block.get("text", "") for block in result.get("content", []) if block.get("type") == "text")


def main():
    sys.path.insert(0, str(HERE))
    import bench_mcp_trial
    from external_app_env import isolated_environment
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--angelo-binary", required=True)
    parser.add_argument("--stock-blender", required=True)
    parser.add_argument("--mcp-server-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--rounds", type=int, default=ROUNDS)
    parser.add_argument("--warmup", type=int, default=WARMUP)
    parser.add_argument("--cold", type=int, default=COLD)
    parser.add_argument("--scenes", default=",".join(SCENES))
    parser.add_argument("--port", type=int, default=9893, help="a port nothing listens on (no Blender MCP add-on)")
    args = parser.parse_args()
    root = Path(args.out).resolve()
    root.mkdir(parents=True, exist_ok=True)
    env = isolated_environment(root / "env")
    binary = Path(args.angelo_binary).resolve(strict=True)
    resources = binary.parents[1] / "Resources" / "5.2"
    python, launcher = resources / "python" / "bin" / "python3.13", resources / "scripts" / "angelo" / "angelo_mcp.py"
    sys.path.insert(0, str(resources / "scripts" / "addons_core"))
    from angelo_agent.external_clients import DEFAULT_LEVELS, AppRegistry
    AppRegistry(env["ANGELO_STATE_DIR"]).connect("other", name="bench", levels=DEFAULT_LEVELS)
    report = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "rounds": args.rounds, "warmup": args.warmup,
              "cold": args.cold, "angelo_binary": str(binary), "stock_blender": args.stock_blender,
              "mcp_server_dir": args.mcp_server_dir, "scenes": {}}
    for scene in args.scenes.split(","):
        folder = root / scene
        folder.mkdir(exist_ok=True)
        files = {}
        for side in ("angelo", "mcp"):
            path = folder / side / "scene.blend"
            path.parent.mkdir(exist_ok=True)
            subprocess.run([str(binary), "--background", "--factory-startup", "--python-expr",
                            FIXTURE.format(extra=SCENES[scene]), "--", str(path)], env=env, capture_output=True,
                           timeout=300, check=True)
            files[side] = path
        angelo = StdioClient([str(python), "-I", str(launcher), "--client", "bench", "--state-dir",
                              env["ANGELO_STATE_DIR"]], env, folder, folder / "angelo.stderr")
        entry = bench_mcp_trial.mcp_config({"mcp_server_dir": args.mcp_server_dir, "stock_blender": args.stock_blender},
                                           args.port)["mcpServers"][bench_mcp_trial.SERVER_NAME]
        mcp = StdioClient([entry["command"], *entry["args"]], {**env, **entry["env"]}, folder, folder / "mcp.stderr")
        try:
            starts = {"angelo": angelo.start(), "mcp": mcp.start()}
            results, errors = run([AngeloSide(angelo, files["angelo"]), McpSide(mcp, files["mcp"], folder / "mcp")],
                                  args.rounds, args.warmup, args.cold)
        finally:
            angelo.close()
            mcp.close()
        report["scenes"][scene] = {"results": results, "errors": errors, "server_start": starts}
        (root / "latency.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0 if not any(any(scene["errors"].values()) for scene in report["scenes"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
