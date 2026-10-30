# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Unreal Angelo Link plugin in a real headless Unreal Editor, against a real background Angelo
(doc/angelo/unreal_link.rst, Validation).

    python3 tests/python/angelo_agent/unreal_link_e2e/editor_run.py --engine "/Users/Shared/Epic Games/UE_5.8" \\
        --binary <Angelo> --out DIR

Makes a throwaway content-only project (the shipped plugin installed into ``Plugins/AngeloLink`` the way Angelo
installs it, ``SourceArt/crate.glb`` made by Angelo), starts ``godot_link_e2e/angelo_side.py`` in a background Angelo
in its durable mode (the Python plugin shows no live scenes), then ``UnrealEditor -nullrhi -unattended`` running
``unreal_side.py`` as the project's start-up script: import, Edit in Angelo, Angelo's edit and save written back as
.glb, the plugin's re-import and package save, Stop. The editor's HOME (its settings and logs), its derived-data cache
and the project live in a short work folder (``--work``; Unreal refuses a cache path longer than 119 characters),
which is deleted afterwards; the reports and logs are copied to ``--out``. Standard library only.

``--live-plugin DIR`` (a built Angelo Link Live plugin: ``RunUAT BuildPlugin -Package=DIR``) adds the live layer:
the built plugin goes into ``Plugins/AngeloLinkLive`` and is enabled, Angelo runs in its live mode and saves only
once the editor reports showing its edit live, and ``unreal_side.py`` checks the edit showed in the open level
before any save, without dirtying the level or the asset, then that Angelo's save replaced the preview.
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
ROOT = HERE.parents[3]
ADDON = ROOT / "scripts/addons_core"
ANGELO_SIDE = HERE.parent / "godot_link_e2e/angelo_side.py"
SECONDS = 1800   # A first start builds the engine's derived data for a new project.

MAKE_ASSET = """
import bpy, os, sys
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_cube_add(size=2)
bpy.context.object.name = "Crate"
bpy.ops.export_scene.gltf(filepath=sys.argv[-1], export_format="GLB")
bpy.ops.mesh.primitive_cube_add(size=0.5, location=(3.0, 0.0, 0.0))
bpy.context.object.name = "Leg"
bpy.ops.export_scene.gltf(filepath=os.path.join(os.path.dirname(sys.argv[-1]), "pair.glb"), export_format="GLB")
"""   # crate.glb: one mesh, the linked asset. pair.glb: two meshes, imported as two assets, which Angelo refuses.

UPROJECT = {"FileVersion": 3, "EngineAssociation": "5.8", "Category": "", "Description": "Angelo Link test",
            "Plugins": [{"Name": "AngeloLink", "Enabled": True}, {"Name": "PythonScriptPlugin", "Enabled": True},
                        {"Name": "Interchange", "Enabled": True}]}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, help="an Unreal Engine 5.4+ install folder (holds Engine/)")
    parser.add_argument("--binary", required=True, help="the Angelo to link with (background only)")
    parser.add_argument("--out", required=True)
    parser.add_argument("--seconds", type=int, default=SECONDS)
    parser.add_argument("--work", default=str(Path.home() / "angelo-ue.noindex"),
                        help="a short folder for the project, the editor's HOME and its cache (deleted afterwards)")
    parser.add_argument("--keep", action="store_true", help="keep the work folder")
    parser.add_argument("--live-plugin", help="a built Angelo Link Live plugin folder (adds the live layer)")
    args = parser.parse_args(argv)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    work = Path(args.work, time.strftime("%H%M%S")).resolve()
    work.mkdir(parents=True)
    try:
        return run(args, out, work)
    finally:
        stop_reporters(work)
        for name in ("unreal.log",):
            if (work / name).exists():
                shutil.copy2(work / name, out / name)
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
            if not any(Path(args.work).iterdir()):
                Path(args.work).rmdir()


def stop_reporters(work):
    """Stop, by PID, any crash reporter a crashed editor of this run left (it names a crash folder in ``work``): one
    spun at full CPU for 43 minutes after a crash (2026-10-29), though ``-nocrashreports`` should prevent it."""
    listing = subprocess.run(["ps", "-Ao", "pid=,args="], capture_output=True, text=True).stdout
    for line in listing.splitlines():
        pid, _, command = line.strip().partition(" ")
        if "CrashReportClient" in command and str(work) in command:
            subprocess.run(["kill", "-TERM", pid])


def run(args, out, work):
    sys.path.insert(0, str(ADDON))
    from angelo_agent import engine_link_assets, unreal_link_assets

    project = work / "LinkTest"
    (project / "SourceArt").mkdir(parents=True)
    (project / "LinkTest.uproject").write_text(json.dumps(UPROJECT, indent=1), encoding="utf-8")
    engine_link_assets.install_plugin(unreal_link_assets, str(project))
    live = bool(args.live_plugin)
    if live:
        shutil.copytree(args.live_plugin, project / "Plugins/AngeloLinkLive",
                        ignore=shutil.ignore_patterns("Intermediate", "HostProject"))
        uproject = dict(UPROJECT, Plugins=UPROJECT["Plugins"] + [{"Name": "AngeloLinkLive", "Enabled": True}])
        (project / "LinkTest.uproject").write_text(json.dumps(uproject, indent=1), encoding="utf-8")
    (project / "Content/Python").mkdir(parents=True)
    shutil.copy2(HERE / "unreal_side.py", project / "Content/Python/init_unreal.py")   # Runs at editor start-up.
    home, unreal_home = out / "angelo-home", work / "home"
    home.mkdir()
    unreal_home.mkdir()
    record = out / "link-record.json"
    base = {key: os.environ[key] for key in ("PATH", "LANG", "TMPDIR", "USER", "LOGNAME", "SHELL") if key in os.environ}
    angelo_env = dict(base, HOME=str(home), CFFIXED_USER_HOME=str(home), ANGELO_STATE_DIR=str(home / "angelo-state"),
                      ANGELO_DISABLE_KEYCHAIN="1", ANGELO_DISABLE_INTRO="1", ANGELO_AGENT_RUNTIME="off",
                      ANGELO_LINK_RECORD=str(record), ANGELO_QA_DIR=str(out), ANGELO_E2E_SECONDS=str(args.seconds),
                      **({"ANGELO_E2E_WAIT_PREVIEW": "1"} if live else {"ANGELO_E2E_DURABLE": "1"}))
    source = project / "SourceArt/crate.glb"
    subprocess.run([args.binary, "--background", "--factory-startup", "--python-expr", MAKE_ASSET, "--", str(source)],
                   check=True, env=angelo_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    report_path = out / "unreal.json"
    unreal_env = dict(base, HOME=str(unreal_home), CFFIXED_USER_HOME=str(unreal_home), ANGELO_LINK_RECORD=str(record),
                      ANGELO_UNREAL_REPORT=str(report_path), ANGELO_UNREAL_SOURCE=str(source),
                      ANGELO_UNREAL_ANGELO_REPORT=str(out / "angelo.json"),
                      ANGELO_UNREAL_SECONDS="240", ANGELO_UNREAL_LIVE="1" if live else "")
    unreal_env["UE-LocalDataCachePath"] = str(work / "ddc")
    unreal_env["UE-SharedDataCachePath"] = "None"
    editor = Path(args.engine, "Engine/Binaries/Mac/UnrealEditor.app/Contents/MacOS/UnrealEditor")
    command = [str(editor), str(project / "LinkTest.uproject"), "-nullrhi", "-unattended", "-nocrashreports",
               "-nosplash", "-nopause", "-nosound", "-NoLiveCoding", "-log", "-abslog=" + str(work / "unreal.log")]
    started = time.monotonic()
    with (out / "angelo.log").open("w") as log:
        side = subprocess.Popen([args.binary, "--background", "--factory-startup", "--python-exit-code", "1",
                                 "--python", str(ANGELO_SIDE)], env=angelo_env, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 60
            while not record.exists() and side.poll() is None and time.monotonic() < deadline:
                time.sleep(0.1)
            try:
                unreal = subprocess.run(command, env=unreal_env, timeout=args.seconds, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL).returncode
            except subprocess.TimeoutExpired:
                unreal = "timeout"
            try:
                angelo_code = side.wait(timeout=60)
            except subprocess.TimeoutExpired:
                angelo_code = "timeout"
        finally:
            if side.poll() is None:
                side.kill()
    unreal_report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {"missing": True}
    angelo_path = out / "angelo.json"
    angelo_report = json.loads(angelo_path.read_text(encoding="utf-8")) if angelo_path.exists() else {"missing": True}
    log_path = work / "unreal.log"
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    report = {"unreal_exit": unreal, "angelo_exit": angelo_code, "seconds": round(time.monotonic() - started),
              "unreal": unreal_report, "angelo_phase": angelo_report.get("phase"),
              "angelo_checks": angelo_report.get("checks"),
              "angelo_error": angelo_report.get("error") or angelo_report.get("traceback"),
              "angelo_link_log": [line for line in log_text.splitlines() if "Angelo Link" in line][:20],
              "python_errors": [line for line in log_text.splitlines() if "LogPython: Error" in line][:20],
              "fatal": [line for line in log_text.splitlines() if "Fatal error" in line or "Critical error" in line][:5],
              "work_mb": round(sum(path.stat().st_size for path in work.rglob("*") if path.is_file()) / 1e6)}
    report["success"] = (bool(unreal_report.get("ok")) and angelo_code == 0
                         and angelo_report.get("phase") == "closed")
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(("PASS" if report["success"] else "FAIL"), "report:", out / "report.json")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
