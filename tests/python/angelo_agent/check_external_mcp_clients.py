# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real AI apps working with Angelo's agent over MCP: Claude Code and Codex (LIVE, billed).

Opt-in only (it runs real models on the accounts the given profiles are signed
in to; nothing signs in). Angelo runs with private TMPDIR, user config and
state; its agent uses the connections folder given (a test profile's) and the
connection the person would pick in the app's External conversation. An app
never calls Angelo's tools: it sends Angelo's agent messages, steers and stops
it, and reads what it sees (doc/angelo/external_mcp.rst). The
apps get Angelo through their own MCP options (``--mcp-config`` for Claude Code,
``-c mcp_servers.angelo...`` for Codex), so no app settings file is written.

A stand-in for the person approves any script card after two seconds and
records it. Each run keeps what the app's model received for the prompt audit
(``scripts/angelo/prompt_audit.py``): Claude Code's session transcript (its
``api-request-shape`` lines), moved out of the profile into
``<name>-transcript.jsonl``, and Codex's rollout, copied into
``<name>-rollouts/``. After the runs Angelo writes the scene's objects; the report holds
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
sys.path.insert(0, str(HERE))
import provider_isolation as isolation  # noqa: E402  (each profile's account before it is billed)

PROMPTS = {
    "claude_agent": ("Use only the angelo MCP tools for this, never a shell. Ask Angelo's agent (angelo_send) to add a "
                     "cube named MCPProbe of size 0.5 at x=0, y=0, z=2. Follow its progress with angelo_read, passing "
                     "the cursor it returns and wait_seconds, until its turn is idle. Then look at the viewport with "
                     "angelo_viewport and tell me in one sentence what you see."),
    "claude_steer": ("Use only the angelo MCP tools. Ask Angelo's agent (angelo_send) to add five cubes named MCPRow "
                     "in a row along x. As soon as it has started (read once with angelo_read), send it a second "
                     "message with angelo_send saying the cubes must be 2 m apart, starting at x=0, y=5, z=0. Then "
                     "read until its turn is idle and tell me in one sentence whether your second message was "
                     "steered or queued, and where the cubes are."),
    "codex_agent": ("Use only the angelo MCP tools. Ask Angelo's agent (angelo_send) to move the object named "
                    "MCPProbe to x=1, y=0, z=2. Follow it with angelo_read (cursor, wait_seconds) until its turn is "
                    "idle, then check the scene with angelo_scene and tell me MCPProbe's location in one sentence."),
    "codex_stop": ("Use only the angelo MCP tools. Ask Angelo's agent (angelo_send) to build a spiral staircase of 40 "
                   "separate steps named MCPStep. Read its progress once with angelo_read (wait_seconds 20), then stop "
                   "it with angelo_stop, read again until its turn is idle, and tell me in one sentence what state it "
                   "reports."),
}
EXPECT = {"claude_agent": lambda objects, calls: "MCPProbe" in objects and "angelo_viewport" in calls,
          "claude_steer": lambda objects, calls: sum(name.startswith("MCPRow") for name in objects) >= 5
          and calls.count("angelo_send") >= 2,
          "codex_agent": lambda objects, calls: [round(v, 3) for v in objects.get("MCPProbe", [None])[:3]] == [1, 0, 2],
          "codex_stop": lambda objects, calls: "angelo_stop" in calls}
# No app may call anything but these (external_mcp_tools.NAMES): a direct tool call is a failure of the contract.
AGENT_TOOLS = frozenset({"angelo_send", "angelo_read", "angelo_stop", "angelo_image", "angelo_scene",
                         "angelo_viewport"})


def launcher_args(resources, state, client):
    return ["-I", str(resources / "scripts/angelo/angelo_mcp.py"), "--client", client, "--state-dir", str(state)]


def run_claude(args, resources, state, name, folder):
    config = {"mcpServers": {"angelo": {"command": str(resources / "python/bin/python3.13"),
                                        "args": launcher_args(resources, state, "claude-code")}}}
    config_path = folder / "claude-mcp.json"
    config_path.write_text(json.dumps(config))
    command = [args.claude, "-p", PROMPTS[name], "--output-format", "stream-json", "--verbose", "--model",
               args.claude_model, "--mcp-config", str(config_path), "--strict-mcp-config",
               "--allowedTools", "mcp__angelo"]
    env = dict(isolation.without_credentials(os.environ),
               CLAUDE_CONFIG_DIR=isolation.checked("claude-profile", args.claude_config_dir))
    run = _run(command, env, folder, name)
    # The session's transcript holds the request shape; it leaves the signed-in profile, which keeps nothing.
    for path in Path(args.claude_config_dir).glob("projects/*/%s.jsonl" % run.pop("session", None)):
        shutil.move(str(path), folder / (name + "-transcript.jsonl"))
    return run


def run_codex(args, resources, state, name, folder):
    python = json.dumps(str(resources / "python/bin/python3.13"))
    arguments = json.dumps(launcher_args(resources, state, "codex"))
    command = [args.codex, "exec", "--skip-git-repo-check", "--model", args.codex_model,
               "--dangerously-bypass-approvals-and-sandbox",   # Nobody answers Codex's own prompts in exec mode.
               "-c", "mcp_servers.angelo.command=" + python, "-c", "mcp_servers.angelo.args=" + arguments,
               "-c", "mcp_servers.angelo.tool_timeout_sec=900", "--json", PROMPTS[name]]
    env = dict(isolation.without_credentials(os.environ), CODEX_HOME=isolation.checked("codex-profile", args.codex_home))
    run = _run(command, env, folder, name)
    for path in Path(args.codex_home).glob("sessions/**/rollout-*%s.jsonl" % run.pop("session", None)):
        (folder / (name + "-rollouts")).mkdir(exist_ok=True)
        shutil.copy2(path, folder / (name + "-rollouts") / path.name)
    return run


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
    # Claude Code's stream names its session (session_id), Codex's its thread (thread.started): the files to keep.
    session = next((line.get("session_id") or line.get("thread_id") for line in lines
                    if line.get("session_id") or line.get("type") == "thread.started"), None)
    return {"exit": done.returncode, "seconds": round(time.monotonic() - started, 1), "final": final[-600:],
            "angelo_calls": calls, "stderr": done.stderr[-800:], "events": len(lines), "session": session or "-"}


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
    # Before anything starts: both apps' profiles and every subscription in the task's store, at their accounts.
    isolation.check_billed([("claude-profile", args.claude_config_dir), ("codex-profile", args.codex_home)],
                           [args.connections_dir])
    env = isolation.without_credentials(isolated_environment(root))
    env["ANGELO_CONNECTIONS_DIR"] = args.connections_dir
    # A connection that reuses a native sign-in finds none: never the person's ~/.codex or ~/.claude.
    env.update(CODEX_HOME=str(root / "no-login" / ".codex"), CLAUDE_CONFIG_DIR=str(root / "no-login" / ".claude"))
    # The person's real HOME: a provider CLI that Angelo's task starts finds its sign-in in the login Keychain only
    # there. State, config and accounts stay private.
    for name in ("HOME", "CFFIXED_USER_HOME"):
        env.pop(name, None)
        if name in os.environ:
            env[name] = os.environ[name]
    binary = Path(args.binary).resolve(strict=True)
    import app_layout
    resources = app_layout.resources(binary)
    sys.path.insert(0, str(resources / "scripts/addons_core"))
    from angelo_agent.external_clients import AppRegistry
    for app in ("claude_code", "codex"):
        AppRegistry(env["ANGELO_STATE_DIR"]).connect(app)
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
        run["scene_shows_it"] = bool(EXPECT[name](final.get("objects", {}), run["angelo_calls"]))
        run["only_agent_tools"] = set(run["angelo_calls"]) <= AGENT_TOOLS
    report["success"] = all(run["exit"] == 0 and run["scene_shows_it"] and run["only_agent_tools"]
                            for run in report["runs"].values())
    (root / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({name: {k: run[k] for k in ("exit", "seconds", "scene_shows_it", "only_agent_tools", "final")}
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
    from angelo_agent import grant_guard  # The test acts as the person (grant_guard.setting).
    grant_guard.harness(setattr, addon.preferences, "allow_local_cli", True)
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
                        from angelo_agent import grant_guard   # The person's Review: the harness stands in.
                        grant_guard.harness(controller.review_script, call)
                    with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
                        __import__("angelo_agent.grant_guard").grant_guard.stand_in().approve(call_id=call.id, session_id=controller.state["id"])
    if (ROOT / "stop").is_file():
        objects = {obj.name: list(obj.location) for obj in bpy.data.objects}
        (ROOT / "scene.json").write_text(json.dumps({"objects": objects, "approvals": seen["approvals"],
                                                     "connection_error": seen.get("connection_error")}))
        bpy.ops.wm.quit_blender()
        return None
    return 0.25


bpy.app.timers.register(_start, first_interval=1.0)
bpy.app.timers.register(_person, first_interval=2.0, persistent=True)
