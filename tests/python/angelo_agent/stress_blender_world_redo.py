# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Stress: commit a World node edit, Undo, Redo, and check that the scene still has its World.

Host Python (the runner)::

    python3 tests/python/angelo_agent/stress_blender_world_redo.py --blender /abs/path/to/Angelo \\
        [--output DIR] [--runs 4] [--workers 4] [--iterations 250] [--storage both] [--no-churn]

Each run is a fresh GUI process (``--factory-startup``) with its own HOME, temporary folder,
Blender user resources and Angelo state; it loops the case of ``test_blender_program_commit_effects``
that failed intermittently (``markers_and_world``): a staged program adds a marker and a node to
the World (the World is replaced by its edited copy), the commit operator pushes Undo, then one
Undo and one Redo. Before the fix in ``readfile.cc`` (doc/angelo/agent_execution.rst, "Undo and
Redo of a replaced World"), Redo sometimes left ``scene.world`` empty. ``--storage both`` runs half
the processes with native stage storage and half with file-backed stages (a World edit always
falls back to a file bundle). Allocation churn between the steps (default on) moves freed memory
around and raises the rate of the allocator coincidence behind the bug. Exit status 1 on any
failed iteration, missing report or abnormal exit.

Correctness stress, not a benchmark. No provider, account, network, user project or paid call.
"""

import json
import os
import sys
import time

try:
    import bpy
except ImportError:
    bpy = None

REPORT_NAME = "world-redo-stress.json"


# --- In the app ------------------------------------------------------------------------------

def _app_main():
    import traceback
    from pathlib import Path
    from unittest.mock import patch

    assert "--factory-startup" in sys.argv and not bpy.app.background, "Use a disposable GUI factory-startup instance"
    here = Path(__file__).resolve().parent
    root = here.parents[2]
    out = Path(os.environ["ANGELO_QA_DIR"])
    os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1")
    sys.path.insert(0, str(root / "scripts/addons_core"))
    sys.path.insert(0, str(here))
    from angelo_agent import native_programs as native, scene_queries as queries, scene_settings
    from program_qa_helpers import args_for, FullStore, no_stages

    iterations = int(os.environ.get("ANGELO_STRESS_ITERATIONS", "250"))
    storage = os.environ.get("ANGELO_STRESS_STORAGE", "file")
    churn = os.environ.get("ANGELO_STRESS_CHURN", "1") == "1"
    report = {"iterations": 0, "failures": [], "storage": storage, "churn": churn, "paid_requests": 0,
              "binary_path": bpy.app.binary_path}

    class Commit(bpy.types.Operator):
        bl_idname = "angelo.world_redo_stress_commit"
        bl_label = "World Redo stress commit"
        bl_options = {"REGISTER", "UNDO"}
        program_id: bpy.props.StringProperty()

        def execute(self, context):
            result = native.commit_job(self.program_id, context.window)
            report["last_commit"] = {key: result.get(key) for key in ("success", "status", "error")}
            return {"FINISHED"} if result.get("success") else {"CANCELLED"}

    def scene():
        return bpy.context.window_manager.windows[0].scene

    def state():
        current = scene()
        world = current.world
        return {"world": world.name if world else None,
                "edited": bool(world and world.node_tree and "Stress gain" in world.node_tree.nodes),
                "markers": [marker.name for marker in current.timeline_markers],
                "worlds": sorted([block.name, block.users] for block in bpy.data.worlds)}

    def move_freed_memory():
        blocks = [bpy.data.worlds.new("Stress churn") for _ in range(3)]
        blocks += [bpy.data.materials.new("Stress churn") for _ in range(3)]
        bpy.data.batch_remove(ids=blocks)

    def settle(job):
        for _ in range(200):
            result = job.poll()
            assert not result.get("error"), result
            if result.get("final") or result.get("ready_to_commit"):
                return result
        raise AssertionError("The staged program did not settle")

    def iteration(index):
        window = bpy.context.window_manager.windows[0]
        bpy.ops.ed.undo_push(message="World Redo stress baseline")
        handle = scene_settings.execute(window, {})["handle"]
        operations = [{"kind": "marker_edit", "target": handle,
                       "parameters": {"operation": "ADD", "name": "Stress marker", "frame": 9}},
                      {"kind": "node_edit", "target": handle,
                       "parameters": {"world": True, "edits": [{"op": "add", "node": "Stress gain",
                                                                 "type": "ShaderNodeValue"}]}}]
        value = args_for(window, operations, store=FullStore())
        settings = scene_settings.execute(window, {})
        value["write_set"].append({"handle": settings["handle"], "revision": settings["revision"]})
        job = native.begin(window, "angelo_scene_program", value, store=FullStore())
        settle(job)
        result = bpy.ops.angelo.world_redo_stress_commit("EXEC_DEFAULT", True, program_id=job.id)
        committed = state()
        steps = []
        for step in (bpy.ops.ed.undo, bpy.ops.ed.redo):
            if churn:
                move_freed_memory()
            step()
            queries.invalidate()
            steps.append(state())
        undone, redone = steps
        passed = ("FINISHED" in result and committed["edited"] and committed["markers"] == ["Stress marker"]
                  and undone["world"] is not None and not undone["edited"] and not undone["markers"]
                  and redone["world"] is not None and redone["edited"] and redone["markers"] == ["Stress marker"]
                  and no_stages())
        if not passed:
            report["failures"].append({"index": index, "committed": committed, "undone": undone, "redone": redone,
                                       "commit": report.get("last_commit")})
            print("ANGELO_WORLD_REDO_FAILURE", json.dumps(report["failures"][-1]), flush=True)
        bpy.ops.ed.undo()
        queries.invalidate()
        report["iterations"] = index + 1

    def run():
        started = time.monotonic()
        try:
            native.register()
            bpy.utils.register_class(Commit)
            bpy.context.preferences.edit.use_global_undo = True
            bpy.context.preferences.filepaths.file_preview_type = "NONE"
            with patch.object(native.BlenderProgramBackend, "memory_staging", storage == "native"), \
                    patch.object(native.BlenderProgramBackend, "fuse_phases", False):
                for index in range(iterations):
                    iteration(index)
            report["success"] = not report["failures"]
        except Exception:
            report.update(success=False, error=traceback.format_exc())
        finally:
            report["seconds"] = round(time.monotonic() - started, 1)
            native.cleanup_all()
            (out / REPORT_NAME).write_text(json.dumps(report, indent=1))
            print("ANGELO_WORLD_REDO_STRESS", json.dumps({key: value for key, value in report.items()
                                                          if key != "failures"}),
                  "failures=%d" % len(report["failures"]), flush=True)
            bpy.ops.wm.quit_blender()

    bpy.app.timers.register(run, first_interval=1)


# --- Host runner -----------------------------------------------------------------------------

def _isolated_env(folder, storage, iterations, churn):
    names = ("home", "tmp", "user", "state", "connections", "library", "qa")
    paths = {name: folder / name for name in names}
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    env = {key: os.environ[key] for key in ("PATH", "LANG", "USER", "LOGNAME", "SHELL", "TERM") if key in os.environ}
    env.update(HOME=str(paths["home"]), CFFIXED_USER_HOME=str(paths["home"]), TMPDIR=str(paths["tmp"]),
               BLENDER_USER_RESOURCES=str(paths["user"]), BLENDER_USER_CONFIG=str(paths["user"] / "config"),
               BLENDER_USER_SCRIPTS=str(paths["user"] / "scripts"),
               BLENDER_USER_DATAFILES=str(paths["user"] / "datafiles"),
               BLENDER_USER_EXTENSIONS=str(paths["user"] / "extensions"),
               ANGELO_QA_DIR=str(paths["qa"]), ANGELO_STATE_DIR=str(paths["state"]),
               ANGELO_CONNECTIONS_DIR=str(paths["connections"]), ANGELO_LIBRARY_DIR=str(paths["library"]),
               ANGELO_DISABLE_KEYCHAIN="1", ANGELO_DISABLE_INTRO="1", ANGELO_DISABLE_HERO="1",
               ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_STRESS_STORAGE=storage,
               ANGELO_STRESS_ITERATIONS=str(iterations), ANGELO_STRESS_CHURN="1" if churn else "0")
    return env, paths["qa"] / REPORT_NAME


def _host_main(argv=None):
    import argparse
    import subprocess
    import tempfile
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    import gui_launch  # Beside this script, which is the first sys.path entry.

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--blender", required=True)
    parser.add_argument("--output")
    parser.add_argument("--runs", type=int, default=4, choices=range(1, 33))
    parser.add_argument("--workers", type=int, default=4, choices=range(1, 9))
    parser.add_argument("--iterations", type=int, default=250)
    parser.add_argument("--storage", choices=("native", "file", "both"), default="both")
    parser.add_argument("--no-churn", action="store_true")
    args = parser.parse_args(argv)
    binary = Path(args.blender).resolve(strict=True)
    output = Path(args.output or tempfile.mkdtemp(prefix="angelo-world-redo-stress-")).resolve()
    output.mkdir(parents=True, exist_ok=True)
    invocation = Path(tempfile.mkdtemp(prefix="invocation-", dir=output))

    def run(index):
        storage = args.storage if args.storage != "both" else ("native", "file")[index % 2]
        folder = invocation / ("run-%02d" % index)
        env, report_path = _isolated_env(folder, storage, args.iterations, not args.no_churn)
        command = [str(binary), "--factory-startup", "--disable-autoexec", "--python-exit-code", "1",
                   *gui_launch.window_flags(input_isolation=False), "--window-geometry", "40", "40", "1000", "700",
                   "--python", str(Path(__file__).resolve())]
        started = time.monotonic()
        with (folder / "run.log").open("w") as log:
            code = subprocess.call(command, env=env, stdout=log, stderr=subprocess.STDOUT,
                                   timeout=60 + 2 * args.iterations)
        text = (folder / "run.log").read_text(errors="replace")
        try:
            value = json.loads(report_path.read_text())
        except (OSError, ValueError) as error:
            value = {"error": "no report: %s" % error}
        problems = [marker for marker in ("Traceback", "BLI_assert", "Assertion failed", "Error: Not freed memory")
                    if marker in text]
        return {"run": index, "storage": storage, "exit": code, "seconds": round(time.monotonic() - started, 1),
                "iterations": value.get("iterations", 0), "failures": len(value.get("failures", [])),
                "success": code == 0 and value.get("success") is True and value.get("iterations") == args.iterations
                and value.get("paid_requests") == 0 and not problems,
                "log_problems": problems, "error": (value.get("error") or "")[-600:]}

    with ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(run, range(args.runs)))
    summary = {"binary": str(binary), "runs": results, "churn": not args.no_churn,
               "iterations": sum(item["iterations"] for item in results),
               "failures": sum(item["failures"] for item in results),
               "success": all(item["success"] for item in results)}
    (invocation / "summary.json").write_text(json.dumps(summary, indent=1))
    for item in results:
        print(json.dumps(item))
    print("ANGELO_WORLD_REDO_STRESS_SUMMARY iterations=%d failures=%d success=%s output=%s"
          % (summary["iterations"], summary["failures"], summary["success"], invocation))
    return 0 if summary["success"] else 1


if bpy is not None:
    _app_main()
elif __name__ == "__main__":
    sys.exit(_host_main())
