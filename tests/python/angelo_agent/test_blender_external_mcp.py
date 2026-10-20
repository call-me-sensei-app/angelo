# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's MCP server in the real app: a real launcher process, as an AI app starts it.

Host driver (no Blender)::

    python3 tests/python/angelo_agent/test_blender_external_mcp.py --binary /abs/Angelo.app/Contents/MacOS/Angelo \\
        --out <folder>

It launches one window process with private HOME, TMPDIR, user config, state,
accounts and runtime directories. Inside it, Connect (the card's own operator)
writes Codex's MCP settings in the private HOME and turns access on; a worker thread then runs the bundled
launcher (``scripts/angelo/angelo_mcp.py`` with the bundle's Python, ``-I``) and
speaks MCP to it: initialize, tools/list, a search, a view (image content), a
cube and a move under Approve for me, a script the fixture approves as the
person would (review, then Approve), a script it declines, and a read after the
decline. Checks: the scene changes exactly as approved, each edit is one Undo
step (three Undos take back the script, the move and the cube in that order),
a snapshot was taken before the first edit, the transcript names the app, no
TCP listener exists, and the AI Providers page draws the card (screenshot).
No provider, model, network or account is used. Contract: doc/angelo/external_mcp.rst.
"""

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
LOG_MARKERS = ("Traceback (most recent call last)", "Error: Not freed memory blocks", "BLI_assert failed", "Segmentation fault")
SCRIPT_RUN = {"description": "Mark the scene", "code": "import bpy\nbpy.context.scene['mcp_fixture'] = 1\nresult = 1"}
SCRIPT_DECLINED = {"description": "Must not run", "code": "import bpy\nbpy.context.scene['mcp_declined'] = 1\nresult = 1"}


def run_host():
    import gui_launch
    import native_suite_paths
    from external_app_env import isolated_environment
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    root = native_suite_paths.fresh_fixture_root(args.out)
    binary = Path(args.binary).resolve(strict=True)
    root.mkdir(mode=0o700)
    env = isolated_environment(root)
    log = root / "angelo.log"
    command = [str(binary), "--factory-startup", "--disable-autoexec", *gui_launch.window_flags(),
               "--window-geometry", "80", "80", "1280", "800", "--python", str(Path(__file__).resolve()),
               "--", "--native-phase", "gui", "--fixture-root", str(root)]
    try:
        with log.open("w") as output:
            result = subprocess.run(command, env=env, stdout=output, stderr=subprocess.STDOUT, timeout=360)
    finally:
        runtime = Path(env["ANGELO_CLI_RUNTIME_DIR"])
        if not runtime.is_relative_to(root):
            shutil.rmtree(runtime, ignore_errors=True)
    text = log.read_text(errors="replace")
    try:
        report = json.loads((root / "external-mcp.json").read_text())
    except (OSError, ValueError):
        report = {"success": False, "error": "The native phase wrote no report"}
    report["exit_code"] = result.returncode
    report["fatal_log_markers"] = [marker for marker in LOG_MARKERS if marker in text]
    report["success"] = bool(report.get("success")) and result.returncode == 0 and not report["fatal_log_markers"]
    (root / "external-mcp.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({key: report.get(key) for key in ("success", "error", "exit_code", "fatal_log_markers",
                                                       "failed_checks", "screenshots", "timings")}, indent=2))
    return 0 if report["success"] else 1


if "--native-phase" not in sys.argv:
    if __name__ == "__main__":
        sys.exit(run_host())
    raise SystemExit


import addon_utils  # noqa: E402
import bpy  # noqa: E402

_parser = argparse.ArgumentParser()
_parser.add_argument("--native-phase", required=True)
_parser.add_argument("--fixture-root", required=True)
ARGS = _parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
OUT = Path(ARGS.fixture_root)
STATE = Path(os.environ["ANGELO_STATE_DIR"])
from angelo_agent import ui  # noqa: E402
from angelo_agent.catalogs import ModelCatalogs  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402

ui._authentication.start = lambda *args, **kwargs: None
type(ui._authentication).start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
Controller.refresh_models = lambda *args, **kwargs: None
ModelCatalogs.refresh = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
(OUT / "blender-temp").mkdir(exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str(OUT / "blender-temp")
report = {"checks": {}, "success": False, "paid_requests": 0, "screenshots": [], "timings": {}}
state = {"phase": "boot", "started": time.monotonic(), "results": {}, "decided": set(), "scripts": 0}


def check(name, condition):
    report["checks"][name] = bool(condition)


class Client:
    def __init__(self, command):
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=open(OUT / "launcher.stderr", "wb"), text=True, bufsize=1,
                                        env={key: value for key, value in os.environ.items()
                                             if not key.startswith("PYTHON")})
        self.next = 1

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

    def call(self, name, arguments):
        started = time.monotonic()
        result = self.request("tools/call", {"name": name, "arguments": arguments})["result"]
        report["timings"].setdefault(name, []).append(round((time.monotonic() - started) * 1000, 1))
        return result


def text_of(result):
    return " ".join(block.get("text", "") for block in result.get("content", []) if block.get("type") == "text")


def sequence(command):
    client = Client(command)
    out = {}
    try:
        out["init"] = client.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})["result"]
        out["tools"] = [tool["name"] for tool in client.request("tools/list")["result"]["tools"]]
        out["search"] = client.call("angelo_scene_search", {"query": "Cube", "coverage": "PAGE", "limit": 5})
        handle = next(iter(__import__("re").findall(r'"(o[1-9][0-9]*)"', text_of(out["search"]))), "o1")
        out["view"] = client.call("angelo_view", {"views": ["ISO"]})
        out["cube"] = client.call("angelo_create_cube", {"name": "MCPCube", "size": 0.5, "location": [3, 0, 0]})
        out["move"] = client.call("angelo_set_transform", {"object": handle, "location": [0, 2, 0]})
        out["script"] = client.call("angelo_execute_python", SCRIPT_RUN)
        out["declined"] = client.call("angelo_execute_python", SCRIPT_DECLINED)
        out["after"] = client.call("angelo_inspect_scene", {})
        lsof = subprocess.run(["lsof", "-nP", "-a", "-p", str(os.getpid()), "-i"], capture_output=True, text=True,
                              timeout=30)
        out["inet"] = lsof.stdout.strip()
    finally:
        client.process.stdin.close()
        client.process.wait(15)
    return out


def external():
    return [c for c in ui._controllers.values() if c.external_client]


def decide():
    """The person at the approval card: approve the first script after reviewing it, decline the second."""
    for controller in external():
        for call in list(controller.pending.values()):
            if call.name != "angelo_execute_python" or not call.approval or call.executing or call.id in state["decided"]:
                continue
            state["decided"].add(call.id)
            state["scripts"] += 1
            report.setdefault("approval_cards", []).append({"tool": call.name, "automatic": call.automatic})
            if state["scripts"] == 1:
                controller.review_script(call)
                with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
                    bpy.ops.angelo.approve(call_id=call.id, session_id=controller.state["id"])
            else:
                controller.decline_tool(call.id)


def capture(name):
    path = OUT / name
    target = next((w for w in bpy.context.window_manager.windows
                   if any(a.type == 'PREFERENCES' for a in w.screen.areas)), bpy.context.window_manager.windows[0])
    with bpy.context.temp_override(window=target):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    report["screenshots"].append(str(path))


def heartbeat():
    try:
        phase = state["phase"]
        if time.monotonic() - state["started"] > 300:
            raise TimeoutError("Fixture timed out in phase " + phase)
        prefs = ui.preferences()
        if phase == "boot":
            check("access_off_by_default", prefs.allow_local_cli is False)
            with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
                bpy.ops.angelo.external_app_connect(app="codex")   # Writes $HOME/.codex/config.toml (private HOME).
            check("connect_turns_access_on", prefs.allow_local_cli is True)
            state["phase"] = "config"
        elif phase == "config":
            from angelo_agent import external_ui
            if "codex" not in external_ui.results:
                return 0.1
            import tomllib
            config = Path(os.environ["HOME"]) / ".codex" / "config.toml"
            entry = tomllib.loads(config.read_text())["mcp_servers"]["angelo"] if config.is_file() else {}
            launcher = Path(entry.get("args", ["", ""])[1])
            check("connect_codex_writes_its_settings", external_ui.results["codex"][0] and bool(entry))
            check("the_entry_runs_the_bundled_launcher_isolated",
                  entry.get("command") == sys.executable and launcher.is_file() and entry["args"][0] == "-I"
                  and "codex" in entry["args"] and str(STATE) in entry["args"] and entry.get("tool_timeout_sec", 0) >= 900)
            check("the_settings_hold_no_key", "key" not in config.read_text().lower())
            state["command"] = [entry["command"], *entry["args"]]
            state["phase"] = "listening"
        elif phase == "listening" and (STATE / "cli/endpoint.json").exists():
            def work():
                try:
                    state["results"]["mcp"] = sequence(state["command"])
                except Exception:
                    state["results"]["mcp"] = {"error": traceback.format_exc()}
            threading.Thread(target=work, daemon=True).start()
            state["phase"] = "running"
        elif phase == "running":
            decide()
            if "mcp" in state["results"]:
                state["phase"] = "checks"
        elif phase == "checks":
            out = state["results"]["mcp"]
            if "error" in out:
                raise RuntimeError(out["error"])
            check("initialize_names_angelos_own_server", out["init"]["serverInfo"]["name"] == "angelo"
                  and "not Blender MCP" in out["init"]["instructions"])
            check("tools_follow_the_apps_levels", {"angelo_task", "angelo_execute_python", "angelo_scene_program"}
                  <= set(out["tools"]) and "angelo_write_file" not in out["tools"])
            check("search_reads_the_live_scene", not out["search"]["isError"] and "Cube" in text_of(out["search"]))
            images = [block for block in out["view"].get("content", []) if block.get("type") == "image"]
            check("view_returns_image_content", not out["view"]["isError"] and images
                  and base64.b64decode(images[0]["data"])[:8] == b"\x89PNG\r\n\x1a\n")
            check("bounded_edits_run_under_approve_for_me", not out["cube"]["isError"] and not out["move"]["isError"]
                  and "MCPCube" in bpy.data.objects
                  and tuple(round(v, 4) for v in bpy.data.objects["MCPCube"].location) == (3, 0, 0)
                  and tuple(round(v, 4) for v in bpy.data.objects["Cube"].location) == (0, 2, 0))
            check("every_script_waited_for_the_persons_review", [c["automatic"] for c in report.get("approval_cards", [])]
                  == [False, False])
            check("approved_script_ran", not out["script"]["isError"] and bpy.context.scene.get("mcp_fixture") == 1)
            check("declined_script_did_not_run_and_says_so", out["declined"]["isError"]
                  and "declined" in text_of(out["declined"]).lower() and "mcp_declined" not in bpy.context.scene)
            check("a_read_after_the_decline_works", not out["after"]["isError"])
            check("no_tcp_listener", "LISTEN" not in out["inet"])
            controllers = external()
            check("one_external_conversation", len(controllers) == 1)
            controller = controllers[0]
            rows = controller.state["messages"]
            check("transcript_names_the_app", any(r.get("request_kind") == "external_request" for r in rows))
            check("snapshot_before_the_first_edit", any(r.get("checkpoint") for r in rows if r.get("role") == "user")
                  or bool(controller.request_checkpoint))
            check("no_provider_started", all(c.provider is None for c in ui._controllers.values()))
            report["external_rows"] = [{k: str(v)[:120] for k, v in r.items() if k in ("role", "tool", "status", "text",
                                        "request_kind", "checkpoint")} for r in rows]
            state["phase"] = "undo"
        elif phase == "undo":
            with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
                bpy.ops.ed.undo()
                after_one = ("mcp_fixture" not in bpy.context.scene, "MCPCube" in bpy.data.objects)
                bpy.ops.ed.undo()
                after_two = (tuple(round(v, 4) for v in bpy.data.objects["Cube"].location) == (0, 0, 0),
                             "MCPCube" in bpy.data.objects)
                bpy.ops.ed.undo()
                after_three = "MCPCube" not in bpy.data.objects
            check("each_edit_is_one_undo_step", after_one == (True, True) and after_two == (True, True) and after_three)
            with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
                bpy.ops.angelo.settings()
            state["phase"] = "page"
            state["page_wait"] = time.monotonic()
            return 1.0
        elif phase == "page":
            from angelo_agent import providers_page_host as page
            target = next((w for w in bpy.context.window_manager.windows
                           if any(a.type == 'PREFERENCES' for a in w.screen.areas)), None)
            if target is None and time.monotonic() - state["page_wait"] < 20:
                return 0.3
            area = next(a for a in target.screen.areas if a.type == 'PREFERENCES')
            region = next(r for r in area.regions if r.type == 'WINDOW')
            plan = page.plan_for(region)
            if plan is None and time.monotonic() - state["page_wait"] < 20:
                return 0.3
            texts = " ".join(op[1] for op in plan.ops if op[0] == "text")
            buttons = [op[2] for op in plan.ops if op[0] == "pbutton"]
            check("page_draws_the_external_apps_card", "External AI apps (MCP)" in texts and "Codex" in texts
                  and "Claude Code" in texts)
            check("page_buttons_say_what_they_do", "Disconnect" in buttons   # Codex is connected; Claude Code is not.
                  and any(label in {"Connect Claude Code", "Copy setup"} for label in buttons))
            report["page_buttons"] = buttons
            with bpy.context.temp_override(window=target, area=area, region=region):
                bpy.ops.view2d.scroll_down(deltay=6000)   # The card is near the page's end.
            state["phase"] = "shot"
            return 0.8
        elif phase == "shot":
            capture("ai-providers-external-apps.png")
            report["success"] = all(report["checks"].values())
            return finish()
        return 0.05
    except Exception:
        report["error"] = traceback.format_exc()
        return finish()


def finish():
    report["failed_checks"] = sorted(name for name, passed in report["checks"].items() if not passed)
    (OUT / "external-mcp.json").write_text(json.dumps(report, indent=2, default=str))
    bpy.app.timers.register(lambda: bpy.ops.wm.quit_blender() and None, first_interval=0.2)
    return None


bpy.app.timers.register(heartbeat, first_interval=0.5, persistent=True)
