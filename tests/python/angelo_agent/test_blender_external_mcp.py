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
speaks MCP to it as an external agent does: initialize and the agent tools; a message with no connection
(``needs_connection``, the conversation's connect signal); then, with a scripted stand-in for the model bound to the
app's External conversation through the person's own send path (``console.submit``), a message that starts a turn
(a cube under Approve for me, a script the fixture approves as the person would, a view), long-poll reads until the
card waits and until the turn ends, the view's picture by id, the scene and the viewport (no row added), a second
message that waits for a turn the stand-in holds, and Stop, after which it is never sent. Checks: the scene changes
exactly as approved, the agent's text and tool rows reach the app, no Angelo tool is ever offered, the turn's first
user row frames the app's words as untrusted, each edit is one Undo step (two Undos take back the script and the
cube), and the AI Providers page draws the card (screenshot). Listeners (2.0.1, the live session page): none until a
send shares the conversation's page; then exactly one, the session page's own server (``web_preview_server``, the port
of the send's ``live_page``), on 127.0.0.1 only; none after the person's Stop sharing on the conversation's notice (and
the next send carries no ``live_page``); one again on 127.0.0.1 after Share again; none once the app disconnects.
Then Copy setup on the card's MCP app row, pressed as a person does (a script's call and the page's action run by a
script are refused first): the clipboard's snippet names a client the launcher serves (initialize and tools/list
through it), holds no key, and access is on; Disconnect revokes it (the same launcher answers "not connected" with
no tools); the card shows the row (screenshot). ``--language ar`` runs it all with the interface in Arabic (right to
left), the labels checked in that language.
No real provider, model, network or account is used. Contract: doc/angelo/external_mcp.rst.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

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


def run_host():
    import gui_launch
    import native_suite_paths
    from external_app_env import isolated_environment
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--language", default="en_US", help="the interface language (ar: right to left)")
    args = parser.parse_args()
    root = native_suite_paths.fresh_fixture_root(args.out)
    binary = Path(args.binary).resolve(strict=True)
    root.mkdir(mode=0o700)
    env = isolated_environment(root)
    log = root / "angelo.log"
    command = [str(binary), "--factory-startup", "--disable-autoexec", *gui_launch.window_flags(),
               "--window-geometry", "80", "80", "1280", "800", "--python", str(Path(__file__).resolve()),
               "--", "--native-phase", "gui", "--fixture-root", str(root), "--language", args.language]
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
_parser.add_argument("--language", default="en_US")
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
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
import host_waits  # noqa: E402  (a wait on its own signal, capped)
bpy.context.preferences.view.show_splash = False
if ARGS.language != "en_US":   # Angelo's own text in that language (its catalogue), Blender's too.
    bpy.context.preferences.view.language = ARGS.language
    bpy.context.preferences.view.use_translate_interface = True
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


class StandIn:
    """A scripted stand-in for the model behind the app's External conversation: a turn adds a cube (Auto runs it),
    asks for a script (the person approves) and takes a view, then replies. A message that says "hold" keeps its
    turn running until Stop."""
    steer_ready = False

    def __init__(self):
        self.texts, self.count, self.release = [], 0, threading.Event()

    def connect(self):
        return "stand-in"

    def call(self, context, name, arguments):
        self.count += 1
        done, out = threading.Event(), []
        context.request_tool("stand-in-%d" % self.count, name, arguments, lambda result: (out.append(result), done.set()))
        done.wait(120)
        return out[0] if out else {}

    def run_request(self, context, native_id, text, observation=None):
        self.texts.append(text)
        if "hold" in text:
            self.release.wait(120)
            return "interrupted" if context.cancelled else "completed"
        self.call(context, "angelo_create_cube", {"name": "MCPCube", "size": 0.5, "location": [3, 0, 0]})
        self.call(context, "angelo_execute_python", SCRIPT_RUN)
        self.call(context, "angelo_view", {"views": ["ISO"]})
        context.emit("message_complete", {"item": "reply", "text": "Made MCPCube and marked the scene."})
        return "completed"

    def stop_request(self, fence):
        self.release.set()

    def close(self):
        pass


def listeners():
    """This process's listening sockets as lsof names them (``127.0.0.1:52345``, ``*:8080``, ``[::1]:80``)."""
    text = subprocess.run(["lsof", "-nP", "-a", "-p", str(os.getpid()), "-i", "-FfnT"], capture_output=True,
                          text=True, timeout=30).stdout
    found, name, listening = [], None, False
    for line in text.splitlines() + ["f"]:
        if line.startswith("f"):
            if listening and name:
                found.append(name)
            name, listening = None, False
        elif line.startswith("n"):
            name = line[1:]
        elif line == "TST=LISTEN":
            listening = True
    return sorted(found)


def until_closed():
    """``listeners()`` once none is left (the server stops off the main thread), or as they are at the cap."""
    try:
        host_waits.until(lambda: not listeners(), what="the session page's listener closing")
    except AssertionError:
        pass
    return listeners()


def pages_server_address():
    """The address Angelo's one local web server (``web_preview_server``, the session page's host) is bound to."""
    from angelo_agent import web_preview_server
    server = web_preview_server._host["server"]
    return tuple(server.server_address[:2]) if server is not None else None


def page_port(result):
    """The port of a send's ``live_page`` (``http://127.0.0.1:<port>/s/<token>/``), or None without one."""
    import re
    match = re.match(r"^http://127\.0\.0\.1:(\d+)/s/[A-Za-z0-9_-]{32}/$", structured(result).get("live_page") or "")
    return int(match.group(1)) if match else None


def sharing(press):
    """Ask the main thread for the person's press on the conversation's notice, and wait until it is done."""
    state["sharing"] = press
    while state.get("sharing_done") != press:
        time.sleep(0.05)


def share():
    """The person's Stop sharing or Share again, pressed on the conversation's own notice (``chat_notices``)."""
    asked = state.get("sharing")
    if not asked or state.get("sharing_done") == asked:
        return
    from angelo_agent import chat_notices
    action = chat_notices.STOP_SHARING if asked == "stop" else chat_notices.SHARE_AGAIN
    chat_notices.dispatch(bpy.context, external()[0], None, action, None)
    state["sharing_done"] = asked


def structured(result):
    return result.get("structuredContent") or {}


def sequence(command):
    client = Client(command)
    out = {}
    try:
        out["init"] = client.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})["result"]
        out["tools"] = [tool["name"] for tool in client.request("tools/list")["result"]["tools"]]
        out["no_connection"] = client.call("angelo_send", {"message": "Make a cube"})
        out["listen_before"] = listeners()   # Access on and an app connected, no page shared: nothing listens.
        state["bind"] = True   # The main thread binds the stand-in for the next send (phase "bind").
        while not state.get("bound"):
            time.sleep(0.05)
        out["sent"] = client.call("angelo_send", {"message": "Make a cube named MCPCube"})
        cursor = structured(out["sent"]).get("cursor")
        out["waiting"] = client.call("angelo_read", {"cursor": cursor, "wait_seconds": 45})
        state["card_read"] = True   # The person reads the card the app saw waiting, then approves it (decide).
        # After the read that waits for the card (lsof takes a moment the turn would run on in): still shared.
        out["listen_shared"], out["server_address"] = listeners(), pages_server_address()
        cursor = structured(out["waiting"]).get("cursor") or cursor
        reads = []
        for _ in range(6):   # Until the turn is idle: each read returns once it settles.
            reads.append(client.call("angelo_read", {"cursor": cursor, "wait_seconds": 45}))
            cursor = structured(reads[-1]).get("cursor") or cursor
            if structured(reads[-1]).get("turn") == "idle":
                break
        out["reads"] = reads
        out["full"] = client.call("angelo_read", {"detail": "full"})
        views = [image for row in structured(out["full"]).get("rows", []) if row.get("tool") == "angelo_view"
                 for image in row.get("images", [])]
        out["image"] = client.call("angelo_image", {"id": views[0]}) if views else {"isError": True}
        out["rows_before_observing"] = len(structured(out["full"]).get("rows", []))
        out["scene"] = client.call("angelo_scene", {})
        out["viewport"] = client.call("angelo_viewport", {})
        out["rows_after_observing"] = len(structured(client.call("angelo_read", {})).get("rows", []))
        sharing("stop")   # The person's Stop sharing on the conversation's notice (main thread).
        out["listen_after_stop"] = until_closed()
        out["held"] = client.call("angelo_send", {"message": "hold this turn"})
        # Returns once the held turn has changed the conversation (it started), never after a fixed sleep.
        client.call("angelo_status", {"task": structured(out["held"]).get("task") or "", "wait_seconds": 10})
        sharing("share")   # Share again: the app's next message brings a new link.
        out["queued"] = client.call("angelo_send", {"message": "never sent"})
        out["listen_again"], out["server_again"] = listeners(), pages_server_address()
        out["stop"] = client.call("angelo_stop", {})
        cursor = structured(out["stop"]).get("cursor")
        out["after_stop"] = client.call("angelo_read", {"wait_seconds": 20})
        out["listen_end"] = listeners()
        out["inet"] = subprocess.run(["lsof", "-nP", "-a", "-p", str(os.getpid()), "-i"], capture_output=True,
                                     text=True, timeout=30).stdout.strip()   # Every inet socket, for the report.
    finally:
        client.process.stdin.close()
        client.process.wait(15)
    return out


def external():
    return [c for c in ui._controllers.values() if c.external_client]


def decide():
    """The person at the approval card: approve each script after reviewing it, once the app's read that waits for
    the card has returned. Approving on the first heartbeat after the card appeared answered it, under the GUI round's
    load, before the app's next read reached Angelo: 12484412217 read ``idle`` with the turn done (a real person
    needs at least a frame to see the card; a read that never returns on it still fails the check, at 45 s)."""
    if not state.get("card_read"):
        return
    for controller in external():
        for call in list(controller.pending.values()):
            if call.name != "angelo_execute_python" or not call.approval or call.executing or call.id in state["decided"]:
                continue
            state["decided"].add(call.id)
            state["scripts"] += 1
            report.setdefault("approval_cards", []).append({"tool": call.name, "automatic": call.automatic})
            from angelo_agent import grant_guard   # The person's Review: the harness stands in.
            grant_guard.harness(controller.review_script, call)
            with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
                __import__("angelo_agent.grant_guard").grant_guard.stand_in().approve(call_id=call.id, session_id=controller.state["id"])


def bind():
    """Once asked: the app's External conversation sends on the stand-in, through the person's own send path
    (``console.submit`` calls ``ui.prepare_controller``, which here binds the stand-in instead of a connection)."""
    if not state.get("bind") or state.get("bound"):
        return
    stand_in = state["stand_in"] = StandIn()
    original = ui.prepare_controller

    def prepare(controller=None):
        if controller is not None and getattr(controller, "external_client", ""):
            controller.provider = stand_in
            return
        return original(controller)
    ui.prepare_controller = prepare
    state["bound"] = True


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
                __import__("angelo_agent.grant_guard").grant_guard.stand_in().external_app_connect(app="codex")   # Writes $HOME/.codex/config.toml (private HOME).
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
            bind()
            decide()
            share()
            if "mcp" in state["results"]:
                state["phase"] = "checks"
        elif phase == "checks":
            out = state["results"]["mcp"]
            if "error" in out:
                raise RuntimeError(out["error"])
            check("initialize_names_angelos_own_server", out["init"]["serverInfo"]["name"] == "angelo"
                  and "Blender MCP" not in out["init"]["instructions"])
            from angelo_agent.external_mcp_tools import AGENT_NAMES
            from angelo_agent.tool_specs import TOOL_MAP
            check("only_the_agent_tools", set(out["tools"]) == AGENT_NAMES and not set(out["tools"]) & set(TOOL_MAP))
            check("no_connection_says_what_to_do", out["no_connection"]["isError"]
                  and structured(out["no_connection"]).get("code") == "needs_connection")
            check("a_message_starts_a_turn", structured(out["sent"]).get("delivered") == "started")
            waiting = structured(out["waiting"])
            # What the read answered, kept: a failed check says what it saw (3b2b492bb4a's red said nothing).
            report["waiting_read"] = {key: waiting.get(key) for key in ("turn", "approval", "needs", "code", "message",
                                                                        "more", "live_page_ended", "conversation",
                                                                        "cursor", "queued")}
            report["waiting_read"]["rows"] = [(row.get("i"), row.get("role"), row.get("tool"), row.get("status"))
                                              for row in waiting.get("rows") or ()]
            report["sent_answer"] = {key: value for key, value in structured(out["sent"]).items() if key != "rows"}
            check("a_read_returns_when_the_card_waits", waiting.get("turn") == "waiting_for_approval"
                  and waiting.get("approval") == {"tool": "angelo_execute_python"} and "needs" in waiting)
            from angelo_agent import swallowed_errors   # A defect a catch-all swallowed (a live page's) is a red.
            report["swallowed"] = swallowed_errors.caught()
            check("no_defect_was_swallowed", not report["swallowed"])
            final = structured(out["reads"][-1]) if out["reads"] else {}
            check("reads_follow_the_turn_to_its_end", final.get("turn") == "idle")
            rows = structured(out["full"]).get("rows", [])
            tools = {row.get("tool"): row.get("status") for row in rows if row.get("role") == "tool"}
            check("the_agents_steps_reach_the_app", tools.get("angelo_create_cube") == "done"
                  and tools.get("angelo_execute_python") == "done" and tools.get("angelo_view") == "done")
            check("the_agents_reply_reaches_the_app", any(row.get("text") == "Made MCPCube and marked the scene."
                                                          for row in rows if row.get("role") == "assistant"))
            check("the_apps_words_read_back_as_sent", any(row.get("role") == "app"
                                                          and row.get("text") == "Make a cube named MCPCube" for row in rows))
            images = [block for block in out["image"].get("content", []) if block.get("type") == "image"]
            check("a_view_the_agent_got_is_read_by_id", not out["image"].get("isError") and images
                  and base64.b64decode(images[0]["data"])[:8] == b"\x89PNG\r\n\x1a\n")
            scene = structured(out["scene"]).get("scene") or {}
            check("the_scene_as_the_agent_sees_it", "MCPCube" in json.dumps(scene))
            viewport = [block for block in out["viewport"].get("content", []) if block.get("type") == "image"]
            check("the_viewport_as_the_agent_sees_it", not out["viewport"].get("isError") and viewport
                  and base64.b64decode(viewport[0]["data"])[:8] == b"\x89PNG\r\n\x1a\n")
            check("observing_adds_no_row", out["rows_after_observing"] == out["rows_before_observing"])
            check("a_message_during_a_turn_waits_for_it", structured(out["queued"]).get("delivered") == "queued")
            check("stop_ends_the_turn_and_sends_nothing_queued", not out["stop"]["isError"]
                  and structured(out["after_stop"]).get("turn") == "idle"
                  and not any("never sent" in text for text in state["stand_in"].texts))
            check("the_scene_changed_as_approved", "MCPCube" in bpy.data.objects
                  and tuple(round(v, 4) for v in bpy.data.objects["MCPCube"].location) == (3, 0, 0)
                  and bpy.context.scene.get("mcp_fixture") == 1)
            check("every_script_waited_for_the_persons_review", [c["automatic"] for c in report.get("approval_cards", [])]
                  == [False])
            report["listeners"] = {key: out[key] for key in ("listen_before", "listen_shared", "listen_after_stop",
                                                              "listen_again", "listen_end")}
            report["inet"] = out["inet"]
            check("no_listener_until_a_page_is_shared", out["listen_before"] == [])
            first, again = page_port(out["sent"]), page_port(out["queued"])
            check("the_only_listener_is_the_session_pages_server_on_loopback", first is not None
                  and out["listen_shared"] == ["127.0.0.1:%d" % first] and out["server_address"] == ("127.0.0.1", first))
            check("stop_sharing_closes_the_listener", out["listen_after_stop"] == []
                  and "live_page" not in structured(out["held"]))
            check("share_again_listens_on_loopback_only_for_a_new_link", again is not None
                  and structured(out["queued"])["live_page"] != structured(out["sent"])["live_page"]
                  and out["listen_again"] == ["127.0.0.1:%d" % again] and out["server_again"] == ("127.0.0.1", again)
                  and out["listen_end"] == out["listen_again"])
            controllers = external()
            check("one_external_conversation", len(controllers) == 1)
            controller = controllers[0]
            rows = controller.state["messages"]
            framed = [r.get("text", "") for r in rows if r.get("request_kind") == "external_task"]
            check("the_agent_reads_the_apps_words_as_untrusted", framed and all("untrusted data" in t for t in framed))
            check("snapshot_before_the_first_edit", any(r.get("checkpoint") for r in rows if r.get("role") == "user")
                  or bool(controller.request_checkpoint))
            report["external_rows"] = [{k: str(v)[:120] for k, v in r.items() if k in ("role", "tool", "status", "text",
                                        "request_kind", "checkpoint")} for r in rows]
            state["phase"] = "undo"
        elif phase == "undo":
            with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
                bpy.ops.ed.undo()
                after_one = ("mcp_fixture" not in bpy.context.scene, "MCPCube" in bpy.data.objects)
                bpy.ops.ed.undo()
                after_two = "MCPCube" not in bpy.data.objects
            check("each_edit_is_one_undo_step", after_one == (True, True) and after_two)
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
            from angelo_agent.ui_text import iface_
            texts = " ".join(op[1] for op in plan.ops if op[0] == "text")
            buttons = [op[2] for op in plan.ops if op[0] == "pbutton"]
            check("page_draws_the_external_apps_card", iface_("External AI apps (MCP)") in texts and "Codex" in texts
                  and "Claude Code" in texts)
            check("page_buttons_say_what_they_do", iface_("Disconnect") in buttons   # Codex is connected.
                  and any(label in {iface_("Connect {app}", app="Claude Code"), iface_("Copy setup")} for label in buttons))
            check("page_offers_any_mcp_app", iface_("MCP app") in texts and "Cursor" in texts and "VS Code" in texts
                  and iface_("Any other app that speaks MCP.") in texts)
            report["page_buttons"] = buttons
            import view_scroll   # view2d.scroll_* ignore a distance: one wheel step a call (view_scroll.py).
            view_scroll.down(target, area, region, 6000)   # The card is near the page's end.
            state["phase"] = "shot"
            return 0.8
        elif phase == "shot":
            capture("ai-providers-external-apps.png")
            state["phase"] = "copy"
        elif phase == "copy":   # The MCP app row's Copy setup: the person's own press on the drawn button.
            from angelo_agent import providers_page_host as page
            from angelo_agent.external_clients import AppRegistry
            check("mcp_app_not_connected_before", "mcp-app" not in AppRegistry(STATE).load())
            from angelo_agent import grant_guard
            target = next(w for w in bpy.context.window_manager.windows
                          if any(a.type == 'PREFERENCES' for a in w.screen.areas))
            area = next(a for a in target.screen.areas if a.type == 'PREFERENCES')
            region = next(r for r in area.regions if r.type == 'WINDOW')
            with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
                try:   # A script's call: refused (Blender raises the operator's ERROR report for a Python caller).
                    scripted = bpy.ops.angelo.external_app_copy_setup(app="other")
                except RuntimeError as error:
                    scripted = str(error)
            try:   # A script driving the page's own action (no press): refused the same way.
                page.act("mcp_copy", "other", target, area, region)
                acted = ""
            except RuntimeError as error:
                acted = str(error)
            check("a_scripted_copy_setup_grants_nothing", (scripted == {'CANCELLED'} or grant_guard.NOT_YOURS in scripted)
                  and grant_guard.NOT_YOURS in acted and "mcp-app" not in AppRegistry(STATE).load())
            plan = page.plan_for(region)
            rect = next((r for r, a, v in (plan.hits if plan else ()) if a == "mcp_copy" and v == "other"), None)
            if rect is None or not (0 <= rect[1] and rect[1] + rect[3] <= region.height):
                state["copy_scrolls"] = state.get("copy_scrolls", 0) + 1
                if state["copy_scrolls"] > 20:
                    raise AssertionError("The MCP app row's Copy setup is not drawn on the page: %r" % (rect,))
                below = rect is None or rect[1] < 0   # Region y grows upward: below the view is negative.
                distance = int(region.height / 2 + (-rect[1] if rect is not None and below else
                                                    rect[1] + rect[3] - region.height if rect is not None else 0))
                import view_scroll   # Toward it, the whole way (view2d.scroll_* move one wheel step whatever asked).
                (view_scroll.down if below else view_scroll.up)(target, area, region, distance)
                return 0.3
            import qa_input   # The person's press (gui_launch's --enable-event-simulate).
            # The page lays out in region pixels (unscaled, left to right): the rect's centre in window pixels.
            qa_input.press(target, region.x + rect[0] + rect[2] / 2, region.y + rect[1] + rect[3] / 2)
            state["copy_pressed"] = time.monotonic()
            state["phase"] = "copy_pressed"
            return 0.2
        elif phase == "copy_pressed":
            from angelo_agent.external_clients import AppRegistry
            if "mcp-app" not in AppRegistry(STATE).load():
                if time.monotonic() - state["copy_pressed"] > 15:
                    raise AssertionError("The press on Copy setup connected no MCP app (presses sent: %r)"
                                         % (__import__("qa_input").sent,))
                return 0.2
            snippet = bpy.context.window_manager.clipboard
            entry = json.loads(snippet)["mcpServers"]["angelo"]
            key = AppRegistry(STATE).key("mcp-app")
            check("copy_setup_connects_the_copied_client", "mcp-app" in entry["args"] and key is not None
                  and AppRegistry(STATE).load()["mcp-app"]["app"] == "other")
            check("the_copied_snippet_holds_no_key", key is not None and key.hex() not in snippet
                  and set(entry) == {"command", "args"} and all(key.hex() not in arg for arg in entry["args"]))
            check("copy_setup_leaves_access_on", ui.preferences().allow_local_cli is True)
            state["copy_command"] = [entry["command"], *entry["args"]]
            state["phase"] = "copy_run"
            return 0.8   # The card redraws with the row connected before the next look (and its screenshot).
        elif phase == "copy_run":
            def handshake(label):
                try:
                    client = Client(state["copy_command"])
                    try:
                        init = client.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
                        tools = client.request("tools/list")["result"]["tools"]
                        state["results"][label] = {"instructions": init["result"]["instructions"], "tools": tools}
                    finally:
                        client.process.stdin.close()
                        client.process.wait(15)
                except Exception:
                    state["results"][label] = {"error": traceback.format_exc()}
            if "copied" not in state["results"] and not state.get("copy_started"):
                capture("ai-providers-mcp-app-connected.png")
                state["copy_started"] = True
                threading.Thread(target=handshake, args=("copied",), daemon=True).start()
            elif "copied" in state["results"] and not state.get("revoked_started"):
                from angelo_agent.external_mcp import NOT_CONNECTED
                copied = state["results"]["copied"]
                check("the_copied_setup_works_in_any_mcp_client", "error" not in copied
                      and copied["instructions"] != NOT_CONNECTED and len(copied["tools"]) > 0)
                with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
                    bpy.ops.angelo.external_app_disconnect(app="other")
                state["revoked_started"] = True
                threading.Thread(target=handshake, args=("revoked",), daemon=True).start()
            elif "revoked" in state["results"]:
                from angelo_agent.external_clients import AppRegistry
                from angelo_agent.external_mcp import NOT_CONNECTED
                revoked = state["results"]["revoked"]
                check("disconnect_revokes_it", "error" not in revoked and revoked["instructions"] == NOT_CONNECTED
                      and revoked["tools"] == [] and AppRegistry(STATE).key("mcp-app") is None)
                state["phase"] = "copy_shot"
                return 0.5
            return 0.1
        elif phase == "copy_shot":
            capture("ai-providers-mcp-app.png")
            with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
                bpy.ops.angelo.external_app_disconnect(app="codex")   # The app's session ends: its page with it.
            state["disconnected"] = time.monotonic()
            state["phase"] = "disconnected"
            return 0.2
        elif phase == "disconnected":
            found = listeners()
            if found and time.monotonic() - state["disconnected"] < host_waits.CAP_S:
                return 0.2   # The pump ends the page on its next tick; the server stops off the main thread.
            report["listeners"]["after_disconnect"] = found
            check("the_listener_closes_when_the_app_disconnects", found == [])
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


# Held until Angelo's start-up (the first tab build its enable schedules) is over: qa_boot.gated.
import qa_input  # noqa: E402  (a click on its way holds the tick: qa_input.after_clicks)
bpy.app.timers.register(qa_boot.gated(qa_input.after_clicks(heartbeat)), first_interval=0.5, persistent=True)
