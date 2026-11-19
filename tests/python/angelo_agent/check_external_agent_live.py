# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every agent endpoint of Angelo's MCP server against a running Angelo whose own agent runs a real model (LIVE, billed).

Opt-in only. No external AI app runs and nothing signs in: a scripted stdio MCP client (this file) starts the bundled
launcher as an app would and drives every agent tool, asserting each answer, while Angelo's own agent works on the
connection the given test store holds (doc/angelo/external_mcp.rst, "Working through Angelo's agent"). Angelo runs
with private TMPDIR, user config and state; a stand-in for the person approves each card after two seconds and
records it::

    python3 check_external_agent_live.py --binary <Angelo> --out <folder ending in .noindex> \\
        --connections-dir <the test profile's connections> --connection <connection id> [--model <id>]

Steps: initialize and tools/list (only the agent tools; no Angelo tool is offered); settings (the connected models; the model chosen); a message
that starts a turn, read with long polls until idle (the cube it asked for exists; tool rows; a picture by id); a
message during a turn (steered or queued, and its result); scene and viewport (no row added); approval narrowed to
Ask and a widening that only asks the person; a new chat; a goal from the app's words; Stop during a turn. The report
holds each step's checks, the rows' sizes and the timings of every call (``latency``).
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
sys.path.insert(0, str(HERE))
import provider_isolation as isolation  # noqa: E402  (the store's accounts before anything is billed)

AGENT_TOOLS = {"angelo_send", "angelo_status", "angelo_read", "angelo_stop", "angelo_image", "angelo_scene",
               "angelo_viewport", "angelo_settings", "angelo_new_chat", "angelo_look", "angelo_window", "angelo_state",
               "angelo_usage"}
TURN_CAP = 600.0   # A turn's working time this check waits for, at most (read again while it runs).


class Client:
    def __init__(self, command, env, cwd, stderr):
        self.process = subprocess.Popen(command, env=env, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=open(stderr, "wb"), text=True, bufsize=1)
        self.next, self.timings = 1, {}

    def request(self, method, params=None):
        identity, self.next = self.next, self.next + 1
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": identity, "method": method,
                                             "params": params or {}}) + "\n")
        self.process.stdin.flush()
        while True:
            line = self.process.stdout.readline()
            if not line:
                raise RuntimeError("the launcher ended before answering " + method)
            message = json.loads(line)
            if message.get("id") == identity:
                return message

    def call(self, name, arguments=None):
        started = time.perf_counter()
        result = self.request("tools/call", {"name": name, "arguments": arguments or {}})["result"]
        self.timings.setdefault(name, []).append(round((time.perf_counter() - started) * 1000, 1))
        return result

    def close(self):
        try:
            self.process.stdin.close()
            self.process.wait(15)
        except (OSError, subprocess.TimeoutExpired):
            self.process.kill()


def value(result):
    return result.get("structuredContent") or {}


def until_idle(client, cursor, rows):
    """Long-poll reads until the turn is idle (or ``TURN_CAP``); every row gathered, the last state."""
    deadline, state = time.monotonic() + TURN_CAP, {}
    while time.monotonic() < deadline:
        state = value(client.call("angelo_read", {"cursor": cursor, "wait_seconds": 45}))
        for row in state.get("rows") or ():
            rows[row["i"]] = row
        cursor = state.get("cursor") or cursor
        if state.get("turn") == "idle":
            break
    return state, cursor


def sequence(client, args, report):
    checks = report["checks"]

    def check(name, condition, detail=None):
        checks[name] = bool(condition)
        if detail is not None and not condition:
            report.setdefault("details", {})[name] = detail

    init = client.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                         "clientInfo": {"name": "angelo-agent-live-check", "version": "1"}})["result"]
    tools = {tool["name"] for tool in client.request("tools/list")["result"]["tools"]}
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
    from angelo_agent.tool_specs import TOOL_MAP
    check("only_the_agent_tools", AGENT_TOOLS <= tools and not tools & set(TOOL_MAP), sorted(tools))
    check("instructions_hand_work_to_angelos_agent", "angelo_send" in init["instructions"]
          and "You don't edit the scene yourself" in init["instructions"]
          and "Blender MCP" not in init["instructions"])
    settings = value(client.call("angelo_settings"))
    models = [m["id"] for m in settings.get("models") or () if m.get("connection_id") == args.connection]
    check("settings_list_the_connected_models", bool(models), settings)
    model = args.model if args.model in models else (models[0] if models else None)
    chosen = value(client.call("angelo_settings", {"model": model, "connection": args.connection})) if model else {}
    check("settings_choose_a_connected_model", chosen.get("model") == model, chosen)
    report["model"] = model

    rows = {}
    sent = client.call("angelo_send", {"message": "Add a cube named MCPProbe of size 0.5 at x=0, y=0, z=2, then look "
                                                  "at it once from the front."})
    check("a_message_starts_a_turn", value(sent).get("delivered") == "started", value(sent))
    state, cursor = until_idle(client, value(sent).get("cursor"), rows)
    check("reads_follow_the_turn_to_idle", state.get("turn") == "idle", state)
    scene = value(client.call("angelo_scene"))
    check("the_cube_exists_in_the_agents_scene", "MCPProbe" in json.dumps(scene), None)
    tool_rows = [row for row in rows.values() if row.get("role") == "tool"]
    check("the_agents_tool_calls_reach_the_app", bool(tool_rows), list(rows.values())[-6:])
    check("the_agent_replies", any(row.get("role") in {"assistant", "system"} for row in rows.values()))
    pictures = [image for row in tool_rows for image in row.get("images") or ()]
    if pictures:
        image = client.call("angelo_image", {"id": pictures[0]})
        check("a_picture_the_agent_got_reads_by_id", not image.get("isError")
              and image["content"][0].get("type") == "image")
    report["first_turn"] = {"rows": len(rows), "tool_rows": len(tool_rows), "pictures": len(pictures),
                            "row_bytes": len(json.dumps(list(rows.values())))}
    before = len(value(client.call("angelo_read", {})).get("rows") or ())
    viewport = client.call("angelo_viewport")
    check("the_viewport_as_the_agent_sees_it", not viewport.get("isError")
          and viewport["content"][0].get("type") == "image", viewport)
    check("observing_adds_no_row", len(value(client.call("angelo_read", {})).get("rows") or ()) == before)
    visibility(client, check, report, before)

    rows = {}
    long_task = client.call("angelo_send", {"message": "Add five cubes named MCPRow1 to MCPRow5 in a row along x, one "
                                                       "at a time, checking each."})
    time.sleep(3.0)
    steer = value(client.call("angelo_send", {"message": "Make them 2 m apart, starting at x=0, y=5, z=0."}))
    check("a_message_during_a_turn_is_steered_or_queued", steer.get("delivered") in {"steered", "queued"}, steer)
    report["steer"] = steer.get("delivered")
    state, cursor = until_idle(client, value(long_task).get("cursor"), rows)
    if steer.get("delivered") == "queued":
        state, cursor = until_idle(client, cursor, rows)
    check("the_steered_work_ends_idle", state.get("turn") == "idle", state)
    check("the_row_exists", sum(name in json.dumps(value(client.call("angelo_scene")))
                                for name in ("MCPRow1", "MCPRow5")) == 2)

    narrowed = value(client.call("angelo_settings", {"approval": "ASK"}))
    check("approval_narrows_to_ask", narrowed.get("approval") == "ASK", narrowed)
    widened = value(client.call("angelo_settings", {"approval": "AUTO_BOUNDED"}))
    check("widening_only_asks_the_person", widened.get("approval") == "ASK" and "needs" in widened, widened)

    fresh = value(client.call("angelo_new_chat"))
    check("a_new_chat_starts_fresh", bool(fresh.get("previous")) and fresh.get("conversation") != fresh.get("previous"),
          fresh)

    rows = {}
    goal = client.call("angelo_send", {"message": "Keep going until there are three UV spheres named MCPGoal1 to "
                                                  "MCPGoal3 in the scene."})
    state, cursor = until_idle(client, value(goal).get("cursor"), rows)
    report["goal_state"] = state.get("goal")   # The agent decides whether to hold one: recorded, not required.
    check("goal_work_ends_idle_or_waits_for_the_person", state.get("turn") == "idle" or "needs" in state, state)

    rows = {}
    stop_task = client.call("angelo_send", {"message": "Build a spiral staircase of 40 separate steps named MCPStep."})
    time.sleep(5.0)
    stopped = value(client.call("angelo_stop"))
    check("stop_is_accepted", stopped.get("turn") in {"stopping", "idle", "running"}, stopped)
    state, cursor = until_idle(client, value(stop_task).get("cursor"), rows)
    check("stop_ends_the_turn", state.get("turn") == "idle", state)


def visibility(client, check, report, before):
    """The read-only visibility tools on the first turn's scene (external_mcp.rst, "Looks", "Window state", "Usage"):
    each answers, adds no row, names no home path; their timings join ``latency`` like every call's."""
    finished = value(client.call("angelo_status")).get("result") or {}
    check("a_finished_task_says_what_changed_and_what_it_cost", "cost" in finished and "verified" in finished,
          finished)
    look = client.call("angelo_look", {"views": ["front", "perspective"], "objects": ["MCPProbe"]})
    images = [item for item in look.get("content") or () if item.get("type") == "image"]
    check("a_look_from_two_angles_returns_two_pictures", not look.get("isError") and len(images) == 2, value(look))
    window = client.call("angelo_window")
    check("the_window_as_the_person_sees_it", not window.get("isError")
          and any(item.get("type") == "image" for item in window.get("content") or ()), value(window))
    state = value(client.call("angelo_state"))
    check("state_reads_the_window_and_the_chat", bool((state.get("window") or {}).get("scene", {}).get("scene"))
          and "model" in (state.get("chat") or {}) and "MCPProbe" in json.dumps(state.get("window")), state)
    usage = value(client.call("angelo_usage"))
    check("usage_has_the_task_and_its_turns", bool(usage.get("tasks")) and bool(usage.get("turns")), usage)
    shown = json.dumps([state, usage, value(look), value(window)])
    check("visibility_names_no_home_path", str(Path.home()) not in shown, None)
    check("visibility_adds_no_row", len(value(client.call("angelo_read", {})).get("rows") or ()) == before)
    report["visibility"] = {"look_pictures": len(images), "state_bytes": len(json.dumps(state)),
                            "usage_turns": len(usage.get("turns") or ())}


def summarize(samples):
    ordered = sorted(samples)
    return {"n": len(ordered), "p50_ms": round(statistics.median(ordered), 1),
            "p75_ms": ordered[min(len(ordered) - 1, int(round(0.75 * (len(ordered) - 1))))], "max_ms": ordered[-1]}


def main():
    from external_app_env import isolated_environment
    import app_layout
    import gui_launch
    parser = argparse.ArgumentParser()
    for name in ("binary", "out", "connections-dir", "connection"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--model", default="claude-opus-5-5")
    args = parser.parse_args()
    root = Path(args.out).resolve()
    root.mkdir(parents=True, exist_ok=True)
    isolation.check_billed([], [args.connections_dir])   # The store's accounts, before anything is billed.
    env = isolation.without_credentials(isolated_environment(root))
    env["ANGELO_CONNECTIONS_DIR"] = args.connections_dir
    env.update(CODEX_HOME=str(root / "no-login" / ".codex"), CLAUDE_CONFIG_DIR=str(root / "no-login" / ".claude"))
    for name in ("HOME", "CFFIXED_USER_HOME"):   # A provider CLI finds its sign-in in the login Keychain only there.
        env.pop(name, None)
        if name in os.environ:
            env[name] = os.environ[name]
    binary = Path(args.binary).resolve(strict=True)
    resources, python = app_layout.resources(binary), app_layout.python(binary)
    sys.path.insert(0, str(resources / "scripts/addons_core"))
    from angelo_agent.external_clients import AppRegistry
    AppRegistry(env["ANGELO_STATE_DIR"]).connect("claude_code")
    for path in (root / "stop", root / "ready", root / "scene.json"):
        path.unlink(missing_ok=True)
    angelo = subprocess.Popen(
        [str(binary), "--factory-startup", "--disable-autoexec", *gui_launch.window_flags("behind"),
         "--window-geometry", "80", "80", "1280", "800", "--python", str(Path(__file__).resolve()), "--",
         "--native-phase", "gui", "--root", str(root), "--connection", args.connection],
        env=env, stdout=(root / "angelo.log").open("w"), stderr=subprocess.STDOUT)
    report = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "binary": str(binary), "checks": {}}
    client = None
    try:
        deadline = time.monotonic() + 200
        while not (root / "ready").is_file():
            if angelo.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("Angelo did not start; see angelo.log")
            time.sleep(0.3)
        client = Client([str(python), "-I", str(resources / "scripts/angelo/angelo_mcp.py"), "--client",
                         "claude-code", "--state-dir", env["ANGELO_STATE_DIR"]], env, root, root / "launcher.stderr")
        try:
            sequence(client, args, report)
        except Exception as error:  # noqa: BLE001 - reported with what ran.
            report["error"] = "%s: %s" % (type(error).__name__, error)
        report["latency"] = {name: summarize(samples) for name, samples in client.timings.items()}
    finally:
        if client is not None:
            client.close()
        (root / "stop").write_text("1")
        try:
            angelo.wait(60)
        except subprocess.TimeoutExpired:
            angelo.kill()
        runtime = Path(env["ANGELO_CLI_RUNTIME_DIR"])
        if not runtime.is_relative_to(root):
            shutil.rmtree(runtime, ignore_errors=True)
    final = json.loads((root / "scene.json").read_text()) if (root / "scene.json").is_file() else {}
    report["approvals"] = final.get("approvals")
    report["failed_checks"] = sorted(name for name, passed in report["checks"].items() if not passed)
    report["success"] = not report["failed_checks"] and "error" not in report
    (root / "report.json").write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps({key: report.get(key) for key in ("success", "error", "failed_checks", "model", "steer",
                                                       "first_turn", "latency")}, indent=2, default=str))
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
CONNECTION = _args[_args.index("--connection") + 1]
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
    # The person's own choice of the conversation's connection (the picker waits for a start-up sign-in check an
    # isolated profile never completes, so the choice is made directly, as check_external_mcp_clients does).
    record = ui.find_connection(CONNECTION)
    host.conversation("claude-code", create=True).choose_connection(
        record["mode"], ui.connection_settings(record["mode"], record=record), "", CONNECTION, [], "")
    (ROOT / "ready").write_text("1")
    return None


def _person():
    """A stand-in for the person: approve each card two seconds after it appears (recorded)."""
    from angelo_agent import grant_guard, ui
    now = time.monotonic()
    for controller in list(ui._controllers.values()):
        for call in list(controller.pending.values()):
            if call.approval and not call.executing and not call.automatic:
                first = seen["first"].setdefault(call.id, now)
                if now - first >= 2.0 and call.id not in seen.get("done", set()):
                    seen.setdefault("done", set()).add(call.id)
                    seen["approvals"].append({"tool": call.name, "client": controller.external_client})
                    if call.name == "angelo_execute_python":
                        grant_guard.harness(controller.review_script, call)   # The person's Review.
                    with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
                        grant_guard.stand_in().approve(call_id=call.id, session_id=controller.state["id"])
    if (ROOT / "stop").is_file():
        (ROOT / "scene.json").write_text(json.dumps({"objects": [obj.name for obj in bpy.data.objects],
                                                     "approvals": seen["approvals"]}))
        bpy.ops.wm.quit_blender()
        return None
    return 0.25


bpy.app.timers.register(_start, first_interval=1.0)
bpy.app.timers.register(_person, first_interval=2.0, persistent=True)
