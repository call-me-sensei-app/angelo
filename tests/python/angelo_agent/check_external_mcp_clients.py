# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real AI apps calling Angelo over MCP: Claude Code and Codex, tool level and task level (LIVE, billed).

Opt-in only (it runs real models on the accounts the given profiles are signed
in to; nothing signs in). Angelo runs with private HOME, TMPDIR, user config and
state; its task level uses the connections folder given (a test profile's) and
the connection the person would pick in the app's External conversation. The
apps get Angelo through their own MCP options (``--mcp-config`` for Claude Code,
``-c mcp_servers.angelo...`` for Codex), so no app settings file is written.

A stand-in for the person approves any script card after two seconds and
records it. After the runs Angelo writes the scene's objects; the report holds
each run's tool calls, final text, exit code and whether the scene shows the
asked-for change::

    python3 check_external_mcp_clients.py --binary <Angelo> --out <folder> \\
        --claude <claude 2.1.301+> --claude-config-dir <signed-in Claude profile> \\
        --codex <codex> --codex-home <signed-in Codex profile> \\
        --connections-dir <Angelo test profile connections> --task-connection <connection id> --task-model <model>
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

PROMPTS = {
    "claude_tools": ("Use only the angelo MCP tools for this, never a shell. Look at the open Blender scene, then add "
                     "a cube named MCPProbe of size 0.5 at x=0, y=0, z=2. Then take a view of the scene and tell me "
                     "in one sentence what you see."),
    "claude_task": ("Use the angelo_task tool to ask Angelo's own agent to do this: \"Add a UV sphere named MCPSphere "
                    "with radius 0.4 at x=2, y=0, z=1.\" Keep checking with angelo_task_status until it is done, then "
                    "tell me its final state in one sentence."),
    "codex_tools": ("Use only the angelo MCP tools for this. Find the object named MCPProbe in the open Blender scene "
                    "with angelo_scene_search, move it to x=1, y=0, z=2 with angelo_set_transform, and tell me its new "
                    "location in one sentence."),
    "codex_task": ("Use the angelo_task tool to ask Angelo's own agent: \"Add a cone named MCPCone at x=-2, y=0, z=0.\" "
                   "Check angelo_task_status until it is done and tell me its final state in one sentence."),
}
EXPECT = {"claude_tools": lambda objects: "MCPProbe" in objects,
          "claude_task": lambda objects: "MCPSphere" in objects or "Moon" in objects,
          "codex_tools": lambda objects: [round(v, 3) for v in objects.get("MCPProbe", [None])[:3]] == [1, 0, 2],
          "codex_task": lambda objects: "MCPCone" in objects}


def launcher_args(resources, state, client):
    return ["-I", str(resources / "scripts/angelo/angelo_mcp.py"), "--client", client, "--state-dir", str(state)]


def run_claude(args, resources, state, name, folder):
    config = {"mcpServers": {"angelo": {"command": str(resources / "python/bin/python3.13"),
                                        "args": launcher_args(resources, state, "claude-code")}}}
    config_path = folder / "claude-mcp.json"
    config_path.write_text(json.dumps(config))
    command = [args.claude, "-p", PROMPTS[name], "--output-format", "stream-json", "--verbose", "--model",
               args.claude_model, "--mcp-config", str(config_path), "--strict-mcp-config",
               "--allowedTools", "mcp__angelo", "--no-session-persistence"]
    env = dict(os.environ, CLAUDE_CONFIG_DIR=args.claude_config_dir)
    return _run(command, env, folder, name)


def run_codex(args, resources, state, name, folder):
    python = json.dumps(str(resources / "python/bin/python3.13"))
    arguments = json.dumps(launcher_args(resources, state, "codex"))
    command = [args.codex, "exec", "--skip-git-repo-check", "--model", args.codex_model,
               "--dangerously-bypass-approvals-and-sandbox",   # Nobody answers Codex's own prompts in exec mode.
               "-c", "mcp_servers.angelo.command=" + python, "-c", "mcp_servers.angelo.args=" + arguments,
               "-c", "mcp_servers.angelo.tool_timeout_sec=900", "--json", PROMPTS[name]]
    env = dict(os.environ, CODEX_HOME=args.codex_home)
    return _run(command, env, folder, name)


def _run(command, env, folder, name):
    started = time.monotonic()
    out = folder / (name + ".jsonl")
    with out.open("w") as handle:
        done = subprocess.run(command, env=env, cwd=folder, stdin=subprocess.DEVNULL, stdout=handle,
                              stderr=subprocess.PIPE, text=True, timeout=900)
    lines = [json.loads(line) for line in out.read_text().splitlines() if line.strip().startswith("{")]
    calls = []
    for line in lines:   # Claude Code's tool_use names (mcp__angelo__<tool>) and Codex's mcp_tool_call items.
        item = line.get("item") or {}
        if item.get("type") == "mcp_tool_call" and line.get("type") == "item.completed":
            calls.append(item.get("tool", ""))
        for block in ((line.get("message") or {}).get("content") or []) if line.get("type") == "assistant" else []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                calls.append(str(block.get("name", "")).replace("mcp__angelo__", ""))
    final = ""
    for line in reversed(lines):
        if line.get("type") == "result":
            final = line.get("result", "")
            break
        item = line.get("item") or {}
        if item.get("type") == "agent_message":
            final = item.get("text", "")
            break
    return {"exit": done.returncode, "seconds": round(time.monotonic() - started, 1), "final": final[-600:],
            "angelo_calls": calls, "stderr": done.stderr[-800:], "events": len(lines)}


def main():
    sys.path.insert(0, str(HERE))
    from external_app_env import isolated_environment
    import gui_launch
    parser = argparse.ArgumentParser()
    for name in ("binary", "out", "claude", "claude-config-dir", "codex", "codex-home", "connections-dir",
                 "task-connection"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--task-model", default="claude-opus-5-5")
    parser.add_argument("--claude-model", default="claude-opus-5-5")
    parser.add_argument("--codex-model", default="gpt-6.1-sol")
    parser.add_argument("--only", nargs="*", default=list(PROMPTS))
    args = parser.parse_args()
    root = Path(args.out).resolve()
    root.mkdir(parents=True, exist_ok=True)
    env = isolated_environment(root)
    env["ANGELO_CONNECTIONS_DIR"] = args.connections_dir
    # The person's real HOME: a provider CLI that Angelo's task starts finds its sign-in in the login Keychain only
    # there. State, config and accounts stay private.
    for name in ("HOME", "CFFIXED_USER_HOME"):
        env.pop(name, None)
        if name in os.environ:
            env[name] = os.environ[name]
    binary = Path(args.binary).resolve(strict=True)
    resources = binary.parents[1] / "Resources" / "5.2"
    sys.path.insert(0, str(resources / "scripts/addons_core"))
    from angelo_agent.external_clients import AppRegistry, DEFAULT_LEVELS
    for app in ("claude_code", "codex"):
        AppRegistry(env["ANGELO_STATE_DIR"]).connect(app, levels=DEFAULT_LEVELS)
    stop, scene = root / "stop", root / "scene.json"
    for path in (stop, scene, root / "ready"):
        path.unlink(missing_ok=True)
    angelo = subprocess.Popen(
        [str(binary), "--factory-startup", "--disable-autoexec", *gui_launch.window_flags("behind"),
         "--window-geometry", "80", "80", "1280", "800", "--python", str(Path(__file__).resolve()), "--",
         "--native-phase", "gui", "--root", str(root), "--connection", args.task_connection, "--model", args.task_model],
        env=env, stdout=(root / "angelo.log").open("w"), stderr=subprocess.STDOUT)
    report = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "runs": {}, "binary": str(binary),
              "claude": subprocess.run([args.claude, "--version"], capture_output=True, text=True).stdout.strip(),
              "codex": subprocess.run([args.codex, "--version"], capture_output=True, text=True).stdout.strip()}
    try:
        deadline = time.monotonic() + 200
        while not (root / "ready").is_file():
            if angelo.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("Angelo did not start; see angelo.log")
            time.sleep(0.3)
        for name in PROMPTS:
            if name not in args.only:
                continue
            runner = run_claude if name.startswith("claude") else run_codex
            report["runs"][name] = runner(args, resources, env["ANGELO_STATE_DIR"], name, root)
            (root / "report.json").write_text(json.dumps(report, indent=2))
    finally:
        stop.write_text("1")
        try:
            angelo.wait(60)
        except subprocess.TimeoutExpired:
            angelo.kill()
        runtime = Path(env["ANGELO_CLI_RUNTIME_DIR"])
        if not runtime.is_relative_to(root):
            shutil.rmtree(runtime, ignore_errors=True)
    final = json.loads(scene.read_text()) if scene.is_file() else {}
    report["scene"] = final
    for name, run in report["runs"].items():
        run["scene_shows_it"] = bool(EXPECT[name](final.get("objects", {})))
    report["success"] = all(run["exit"] == 0 and run["scene_shows_it"] for run in report["runs"].values())
    (root / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({name: {k: run[k] for k in ("exit", "seconds", "scene_shows_it", "final")}
                      for name, run in report["runs"].items()}, indent=2))
    print("approvals:", final.get("approvals"))
    return 0 if report["success"] else 1


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
CONNECTION, MODEL = _args[_args.index("--connection") + 1], _args[_args.index("--model") + 1]
seen = {"approvals": [], "first": {}}


def _start():
    addon = bpy.context.preferences.addons.get("angelo_agent")
    if addon is None:
        return 0.3
    addon.preferences.allow_local_cli = True
    from angelo_agent import external_host, ui
    host = external_host._host
    if host is None or not (Path(os.environ["ANGELO_STATE_DIR"]) / "cli/endpoint.json").is_file():
        return 0.3
    # The person picks each app's chat connection in its model picker. The picker waits for the private profile's
    # start-up sign-in check, which an isolated HOME never completes, so the choice is made directly.
    record = ui.find_connection(CONNECTION)
    for label in ("claude-code", "codex"):
        host.conversation(label, create=True).choose_connection(
            record["mode"], ui.connection_settings(record["mode"], record=record), "", CONNECTION, [], "")
    (ROOT / "ready").write_text("1")
    return None


def _person():
    """A stand-in for the person: approve each script card two seconds after it appears (recorded)."""
    from angelo_agent import ui
    now = time.monotonic()
    for controller in list(ui._controllers.values()):
        for call in list(controller.pending.values()):
            if call.approval and not call.executing and not call.automatic:
                first = seen["first"].setdefault(call.id, now)
                if now - first >= 2.0:
                    seen["approvals"].append({"tool": call.name, "client": controller.external_client})
                    if call.name == "angelo_execute_python":
                        controller.review_script(call)
                    with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
                        bpy.ops.angelo.approve(call_id=call.id, session_id=controller.state["id"])
    if (ROOT / "stop").is_file():
        objects = {obj.name: list(obj.location) for obj in bpy.data.objects}
        (ROOT / "scene.json").write_text(json.dumps({"objects": objects, "approvals": seen["approvals"],
                                                     "connection_error": seen.get("connection_error")}))
        bpy.ops.wm.quit_blender()
        return None
    return 0.25


bpy.app.timers.register(_start, first_interval=1.0)
bpy.app.timers.register(_person, first_interval=2.0, persistent=True)
