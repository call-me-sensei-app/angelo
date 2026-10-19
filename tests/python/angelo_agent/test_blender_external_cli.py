# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native GUI fixture: real ``angelo`` processes against the live local CLI listener.

Run outside Blender, with one fresh disposable directory under the short temp
root (``native_suite_paths.short_temp_root()``: ``/private/tmp`` on macOS)::

    python3 tests/python/angelo_agent/test_blender_external_cli.py \\
        --binary /absolute/path/to/Angelo --out <short temp root>/angelo-external-cli-<id>

The runner launches the actual GUI binary with factory startup, event
simulation and private HOME, TMPDIR, user config, agent state, accounts and
CLI runtime directories. Inside, the fixture enables Preferences > Allow local
CLI access and real client processes (bundled Python, ``-I``) exercise status,
tools, reads, refusals, a stale document after a file load, ``session new``,
the kill switch and toggle-off. The scene must stay unchanged. No provider,
inference, network or paid service is used; logs are checked for native
assertions, tracebacks and leaks.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[3]
LAUNCHER = ROOT / "scripts/angelo/angelo_cli.py"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(1, str(Path(__file__).resolve().parent))
from angelo_agent import product_env  # noqa: E402  (standard library only)
import native_suite_paths  # noqa: E402  (standard library only)
LOG_MARKERS = ("BLI_assert failed", "Segmentation fault", "Traceback", "Not freed memory blocks")
# external_cli.SUN_PATH_LIMIT (macOS sun_path is 104 bytes with the NUL) and
# the length of the listener's per-state socket name.
SUN_PATH_LIMIT = 103
SOCKET_NAME = "angelo-cli-" + "0" * 16 + ".sock"


def runtime_directory(root):
    """``root/run`` when the socket path fits; deep roots (run_native_suites.py) get a short private one."""
    run = root / "run"
    if len(os.fsencode(run / SOCKET_NAME)) <= SUN_PATH_LIMIT:
        return run
    short = native_suite_paths.short_temp_root()
    return short / ("angelo-cli-run-" + hashlib.sha256(os.fsencode(root)).hexdigest()[:12])


def isolated_environment(root, run=None):
    """The app's and clients' environment; ``run`` reuses the runtime directory the host already chose."""
    folders = {name: root / name for name in ("home", "tmp", "config", "resources", "state", "accounts")}
    folders["run"] = Path(run) if run else runtime_directory(root)
    for folder in folders.values():
        folder.mkdir(mode=0o700, exist_ok=True)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("BLENDER_", product_env.PREFIX, *product_env.LEGACY_PREFIXES, "PYTHON"))}
    env.update(HOME=str(folders["home"]), TMPDIR=str(folders["tmp"]) + "/",
               BLENDER_USER_RESOURCES=str(folders["resources"]), BLENDER_USER_CONFIG=str(folders["config"]),
               ANGELO_STATE_DIR=str(folders["state"]), ANGELO_CONNECTIONS_DIR=str(folders["accounts"]),
               ANGELO_CLI_RUNTIME_DIR=str(folders["run"]), ANGELO_DISABLE_DEFAULT_CHAT="1",
               ANGELO_QA_DIR=str(root))
    return env


def run_host():
    import gui_launch
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--client-python", help="default: the app bundle's Python")
    parser.add_argument("--objects", type=int, default=0, help="extra objects for a scale cohort (e.g. 2000)")
    args = parser.parse_args()
    root = native_suite_paths.fresh_fixture_root(args.out)
    binary = Path(args.binary).resolve(strict=True)
    python = Path(args.client_python or binary.parents[1] / "Resources/5.2/python/bin/python3.13").resolve(strict=True)
    root.mkdir(mode=0o700)
    env = isolated_environment(root)
    log = root / "angelo.log"
    command = [str(binary), "--factory-startup", "--disable-autoexec", *gui_launch.window_flags(),
               "--window-geometry", "80", "80", "1280", "800", "--python", str(Path(__file__).resolve()),
               "--", "--native-phase", "gui", "--fixture-root", str(root), "--client-python", str(python),
               "--objects", str(args.objects)]
    try:
        with log.open("w") as output:
            result = subprocess.run(command, env=env, stdout=output, stderr=subprocess.STDOUT, timeout=300)
    finally:
        runtime = Path(env["ANGELO_CLI_RUNTIME_DIR"])
        if not runtime.is_relative_to(root):
            shutil.rmtree(runtime, ignore_errors=True)  # The short directory outside the fixture root.
    text = log.read_text(errors="replace")
    try:
        report = json.loads((root / "external-cli.json").read_text())
    except (OSError, ValueError):
        report = {"success": False, "error": "The native phase wrote no report"}
    report["exit_code"] = result.returncode
    report["fatal_log_markers"] = [marker for marker in LOG_MARKERS if marker in text]
    report["isolated_tmp_entries"] = sorted(path.name for path in (root / "tmp").iterdir())
    socket_left = bool(report.get("socket_at_quit")) and Path(report["socket_at_quit"]).exists()
    report["exit_withdrew_endpoint_and_socket"] = (not (root / "state/cli/endpoint.json").exists()
                                                   and bool(report.get("socket_at_quit")) and not socket_left)
    report["success"] = (bool(report.get("success")) and result.returncode == 0 and not report["fatal_log_markers"]
                         and report["exit_withdrew_endpoint_and_socket"])
    (root / "external-cli.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({key: report.get(key) for key in ("success", "error", "exit_code", "fatal_log_markers",
                                                       "failed_checks", "latency", "isolated_tmp_entries",
                                                       "exit_withdrew_endpoint_and_socket")}, indent=2))
    return 0 if report["success"] else 1


if "--native-phase" not in sys.argv:
    if __name__ == "__main__":
        sys.exit(run_host())
    raise SystemExit


import traceback  # noqa: E402

import addon_utils  # noqa: E402
import bpy  # noqa: E402

assert "--factory-startup" in sys.argv and "--enable-event-simulate" in sys.argv and not bpy.app.background
_parser = argparse.ArgumentParser()
_parser.add_argument("--native-phase", required=True)
_parser.add_argument("--fixture-root", required=True)
_parser.add_argument("--client-python", required=True)
_parser.add_argument("--objects", type=int, default=0)
ARGS = _parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
OUT = Path(ARGS.fixture_root)
STATE = Path(os.environ["ANGELO_STATE_DIR"])
# The host chose the runtime directory; outside macOS the short root follows TMPDIR, which differs in the app.
CLIENT_ENV = isolated_environment(OUT, os.environ["ANGELO_CLI_RUNTIME_DIR"])
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import ui, external_host, team_account_ui  # noqa: E402
from angelo_agent.catalogs import ModelCatalogs  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402

# Only external discovery is stubbed; the listener, controller and pump are production paths.
ui._authentication.start = lambda *args, **kwargs: None
type(ui._authentication).start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
Controller.refresh_models = lambda *args, **kwargs: None
ModelCatalogs.refresh = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.filepaths.file_preview_type = 'NONE'
(OUT / "blender-temp").mkdir(exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str(OUT / "blender-temp")
report = {"checks": {}, "success": False, "paid_requests": 0, "binary": bpy.app.binary_path,
          "host_modules": str(Path(ui.__file__).parent), "client_python": ARGS.client_python, "calls": []}
state = {"phase": "boot", "started": time.monotonic(), "worker": None, "results": {}}


def check(name, condition):
    report["checks"][name] = bool(condition)


def write_report():
    report["failed_checks"] = sorted(name for name, passed in report["checks"].items() if not passed)
    (OUT / "external-cli.json").write_text(json.dumps(report, indent=2, default=str))


def cli(*argv, label="fixture", state_dir=STATE):
    command = [ARGS.client_python, "-I", str(LAUNCHER), "--state-dir", str(state_dir), "--client", label, *argv]
    started = time.monotonic()
    done = subprocess.run(command, capture_output=True, text=True, timeout=120, env=CLIENT_ENV)
    elapsed = round((time.monotonic() - started) * 1000, 1)
    try:
        envelope = json.loads(done.stdout) if "--format" not in argv else {"text": done.stdout}
    except ValueError:
        envelope = {"unparsed": done.stdout[-2000:], "stderr": done.stderr[-2000:]}
    report["calls"].append({"argv": list(argv)[:3], "exit": done.returncode, "code": envelope.get("code"),
                            "client_ms": elapsed, "timings": envelope.get("timings")})
    return done.returncode, envelope


def in_worker(name, steps):
    def run():
        try:
            state["results"][name] = steps()
        except Exception:
            state["results"][name] = {"error": traceback.format_exc()}
    state["worker"] = threading.Thread(target=run, daemon=True)
    state["worker"].start()


def scene_signature():
    objects = []
    for obj in sorted(bpy.data.objects, key=lambda item: item.name):
        mesh = ""
        if obj.type == 'MESH':
            mesh = hashlib.sha256(b"".join(v.co.to_tuple(6).__repr__().encode() for v in obj.data.vertices)).hexdigest()
        objects.append((obj.name, obj.type, tuple(round(v, 6) for row in obj.matrix_world for v in row), mesh))
    return {"objects": objects, "counts": [len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.materials)],
            "frame": bpy.context.scene.frame_current, "dirty": bpy.data.is_dirty}


def external_controllers():
    return [c for c in ui._controllers.values() if c.external_client]


def refuses_model_turn(controller):
    try:
        controller.send("A model turn must never start in an External conversation")
    except RuntimeError:
        return not controller.busy and controller.provider is None
    return False


class Layout:
    """Records what the AI Providers preferences draw, without a window."""
    def __init__(self, seen):
        self.seen = seen

    def __getattr__(self, name):
        def call(*args, **kwargs):
            if name == "prop":
                self.seen.append(args[1])
            return self
        return call


def sequence_a():
    out = {"status": cli("status"), "tools": cli("tools"),
           "schema": cli("tools", "--tool", "angelo_scene_search", "--schema"),
           "search": cli("call", "angelo_scene_search", '{"query": "Cube", "limit": 10}', "--id", "search-1")}
    out["reads"] = [cli("call", "angelo_inspect_scene") for _ in range(10)]
    out["searches"] = [cli("call", "angelo_scene_search", '{"limit": 50}') for _ in range(5)]
    out["mutation"] = cli("call", "angelo_create_cube", '{"name": "CLI", "size": 1, "location": [0, 0, 0]}', "--id", "m-1")
    out["python"] = cli("call", "angelo_execute_python", '{"description": "x", "code": "pass"}', "--id", "m-2")
    out["file"] = cli("call", "angelo_read_file", '{"path": "cli/endpoint.json", "start": 0, "length": 100}')
    out["unknown"] = cli("call", "angelo_unknown_tool")
    out["observe"] = cli("observe")
    out["text"] = cli("--format", "text", "call", "angelo_inspect_scene")
    record = json.loads((STATE / "cli/endpoint.json").read_text())
    forged = OUT / "forged-state"
    (forged / "cli").mkdir(parents=True, mode=0o700)
    descriptor = os.open(forged / "cli/endpoint.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(dict(record, token="0" * 64), handle)
    out["forged"] = cli("status", state_dir=forged)
    lsof = subprocess.run(["lsof", "-nP", "-a", "-p", str(os.getpid()), "-i"], capture_output=True, text=True, timeout=30)
    out["inet_sockets"] = lsof.stdout.strip()
    return out


def sequence_b(old_document, old_conversation):
    out = {"stale": cli("call", "angelo_inspect_scene"), "status": cli("status"),
           "expect": cli("call", "angelo_inspect_scene", "--expect-document", old_document)}
    out["rotate"] = cli("session", "new")
    out["fresh"] = cli("call", "angelo_scene_search", '{"limit": 10}')
    out["old_guard"] = cli("call", "angelo_inspect_scene", "--conversation", old_conversation)
    return out


def phase_after_sequence_a(results):
    exit_status, status = results["status"]
    check("status_ok_before_any_conversation", exit_status == 0 and status["result"]["conversation"] is None)
    check("envelope_has_every_documented_field", set(status) >= {"cli_protocol", "ok", "code", "exit", "retry",
          "toolset_version", "document", "conversation", "call", "result", "message"})
    _, tools = results["tools"]
    allowed = {tool["name"] for tool in tools["result"]["allowed"]}
    check("tools_lists_13_read_only_tools", len(allowed) == 13 and all(t["read_only"] for t in tools["result"]["allowed"]))
    check("tools_excludes_reviewed_python_with_reason", any(t["name"] == "angelo_execute_python" and t["reason"]
                                                           for t in tools["result"]["excluded"]))
    check("tools_schema_has_input_schema_and_digest", "inputSchema" in results["schema"][1]["result"]["allowed"][0])
    exit_search, search = results["search"]
    check("scene_search_reads_the_live_scene", exit_search == 0 and "Cube" in json.dumps(search["result"]))
    check("read_results_carry_no_private_state_path", str(STATE) not in json.dumps(search))
    check("ten_sequential_reads_ok", all(code == 0 and value["code"] == "ok" for code, value in results["reads"]))
    for key in ("mutation", "python", "file"):
        code, value = results[key]
        check(f"{key}_refused_with_exit_4", code == 4 and value["code"] == "refused")
    check("unknown_tool_invalid_exit_2", results["unknown"][0] == 2)
    check("observe_not_yet_available_exit_9", results["observe"][0] == 9)
    check("text_format_reports_ok", results["text"][0] == 0 and results["text"][1]["text"].startswith("ok (exit 0"))
    check("forged_token_unavailable_exit_8", results["forged"][0] == 8)
    # The team sign-in callback (team_signin.py) is Angelo's only network
    # listener and exists only while a sign-in waits for the browser.
    check("no_team_sign_in_waiting", not team_account_ui.signing_in())
    check("no_tcp_listener_in_angelo", "LISTEN" not in results["inet_sockets"])
    report["inet_sockets"] = results["inet_sockets"]
    externals = external_controllers()
    check("one_external_conversation_for_the_label", len(externals) == 1)
    controller = externals[0]
    title = ui._sessions.records[controller.state["id"]]["title"]
    check("switcher_title_is_external_label", title == "External: fixture")
    activities = [m for m in controller.state["messages"] if m["role"] == "activity"]
    check("reads_recorded_in_external_transcript", any(m["tool"] == "angelo_scene_search" and m["status"] == "completed"
                                                      for m in activities))
    check("refused_calls_never_reached_the_controller", not any(m["tool"] in {"angelo_create_cube",
          "angelo_execute_python", "angelo_read_file"} for m in activities)
          and not any(key.endswith(("/m-1", "/m-2")) for key in controller.state["results"]))
    check("reads_reserved_no_mutation_ids", controller.state["invocations"]["entries"] == {})
    check("external_request_closed_after_reads", not controller.busy and not controller.pending)
    check("scene_unchanged_by_reads", scene_signature() == state["baseline"])
    check("scene_searches_ok", all(code == 0 for code, _ in results["searches"]))
    report["latency"] = {"objects": len(bpy.data.objects)}
    for tool in ("angelo_inspect_scene", "angelo_scene_search"):
        entries = [entry for entry in report["calls"] if entry["argv"][:2] == ["call", tool] and entry.get("timings")]
        timings = [entry["timings"] for entry in entries]
        report["latency"][tool] = {"samples": len(timings),
            "host_ms_p50": statistics.median(t["host_ms"] for t in timings), "host_ms_max": max(t["host_ms"] for t in timings),
            "queue_ms_p50": statistics.median(t["queue_ms"] for t in timings),
            "service_total_ms_p50": statistics.median(t["total_ms"] for t in timings),
            "client_process_ms_p50": statistics.median(entry["client_ms"] for entry in entries),
            "client_process_ms_max": max(entry["client_ms"] for entry in entries)}
    state["old_document"] = search["document"]
    state["old_conversation"] = controller.state["id"]


def phase_after_sequence_b(results):
    check("call_after_load_is_stale_exit_5", results["stale"][0] == 5 and results["stale"][1]["retry"] == "after_observe")
    check("status_reports_invalid_document", results["status"][1]["result"]["conversation"]["document_valid"] is False)
    check("expect_document_mismatch_is_stale", results["expect"][0] == 5)
    rotated = results["rotate"][1]
    check("session_new_rotates_explicitly", results["rotate"][0] == 0 and rotated["result"]["previous"] == state["old_conversation"])
    check("fresh_conversation_reads_new_document", results["fresh"][0] == 0
          and results["fresh"][1]["conversation"] == rotated["conversation"]
          and results["fresh"][1]["document"] != state["old_document"])
    check("old_conversation_guard_is_stale", results["old_guard"][0] == 5)
    check("old_transcript_stays_readable", state["old_conversation"] in ui._sessions.records
          and rotated["conversation"] in ui._sessions.records)


def heartbeat():
    try:
        phase = state["phase"]
        if time.monotonic() - state["started"] > 240:
            raise TimeoutError("Fixture timed out in phase " + phase)
        prefs = ui.preferences()
        if phase == "boot":
            if ARGS.objects:
                mesh = bpy.data.meshes["Cube"]  # Shared data keeps the cohort light; objects are what scale.
                for index in range(ARGS.objects):
                    obj = bpy.data.objects.new(f"Cohort{index:05d}", mesh)
                    obj.location = (index % 50, index // 50, 0)
                    bpy.context.scene.collection.objects.link(obj)
            bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "document-b.blend"), copy=True)
            check("cli_access_off_by_default", prefs.allow_local_cli is False and not (STATE / "cli/endpoint.json").exists())
            seen = []
            ui.draw_ai_providers(Layout(seen), bpy.context)
            check("preferences_show_the_toggle", "allow_local_cli" in seen)
            state["baseline"] = scene_signature()
            prefs.allow_local_cli = True
            state["phase"] = "listening"
        elif phase == "listening" and (STATE / "cli/endpoint.json").exists():
            endpoint = STATE / "cli/endpoint.json"
            record = json.loads(endpoint.read_text())
            check("endpoint_owner_only", os.stat(endpoint).st_mode & 0o777 == 0o600)
            check("runtime_directory_private_and_isolated", os.stat(Path(record["socket"]).parent).st_mode & 0o777 == 0o700
                  and Path(record["socket"]).parent == Path(os.environ["ANGELO_CLI_RUNTIME_DIR"]))
            check("socket_owner_only", os.stat(record["socket"]).st_mode & 0o777 == 0o600)
            in_worker("a", sequence_a)
            state["phase"] = "sequence_a"
        elif phase == "sequence_a" and "a" in state["results"]:
            results = state["results"]["a"]
            if "error" in results:
                raise RuntimeError(results["error"])
            phase_after_sequence_a(results)
            with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
                bpy.ops.wm.open_mainfile(filepath=str(OUT / "document-b.blend"), load_ui=False)
            state["phase"] = "loaded"
            return 0.3
        elif phase == "loaded":
            state["baseline_b"] = scene_signature()
            in_worker("b", lambda: sequence_b(state["old_document"], state["old_conversation"]))
            state["phase"] = "sequence_b"
        elif phase == "sequence_b" and "b" in state["results"]:
            results = state["results"]["b"]
            if "error" in results:
                raise RuntimeError(results["error"])
            phase_after_sequence_b(results)
            state["socket"] = json.loads((STATE / "cli/endpoint.json").read_text())["socket"]
            prefs.allow_local_cli = False
            state["phase"] = "closing"
            return 0.3
        elif phase == "closing":
            check("toggle_off_removes_endpoint_and_socket", not (STATE / "cli/endpoint.json").exists()
                  and not Path(state["socket"]).exists())
            in_worker("c", lambda: {"off": cli("status")})
            state["phase"] = "sequence_c"
        elif phase == "sequence_c" and "c" in state["results"]:
            check("status_after_toggle_off_unavailable_exit_8", state["results"]["c"]["off"][0] == 8)
            os.environ[external_host.KILL_SWITCH] = "1"
            prefs.allow_local_cli = True
            state["phase"] = "kill_switch"
            return 0.5
        elif phase == "kill_switch":
            check("kill_switch_keeps_listener_closed", not (STATE / "cli/endpoint.json").exists()
                  and "ANGELO_DISABLE_LOCAL_CLI" in external_host.status_text())
            prefs.allow_local_cli = False
            del os.environ[external_host.KILL_SWITCH]
            check("scene_unchanged_after_all_cli_work", scene_signature() == state["baseline_b"])
            check("no_provider_started", all(c.provider is None for c in ui._controllers.values()))
            check("external_chats_refuse_model_turns", all(refuses_model_turn(c) for c in external_controllers()))
            prefs.allow_local_cli = True  # Quit while listening: exit must withdraw the record and socket.
            state["phase"] = "quit_listening"
        elif phase == "quit_listening" and (STATE / "cli/endpoint.json").exists():
            report["socket_at_quit"] = json.loads((STATE / "cli/endpoint.json").read_text())["socket"]
            report["success"] = all(report["checks"].values())
            return finish()
        return 0.05
    except Exception:
        report["error"] = traceback.format_exc()
        return finish()


def finish():
    write_report()
    bpy.app.timers.register(lambda: bpy.ops.wm.quit_blender() and None, first_interval=0.2)
    return None


bpy.app.timers.register(heartbeat, first_interval=0.5, persistent=True)
