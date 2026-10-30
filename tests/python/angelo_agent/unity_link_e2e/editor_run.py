# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Unity Angelo Link package in a real Unity Editor, against a real background Angelo (doc/angelo/unity_link.rst,
Validation).

    python3 tests/python/angelo_agent/unity_link_e2e/editor_run.py --unity <Unity.app> --binary <Angelo> --out DIR

Makes a throwaway Unity project in ``DIR`` (the shipped package embedded in ``Packages/``, a manifest of the
editor's built-in modules only, so no registry package is resolved, ``editor/BatchTest.cs`` in ``Assets/Editor``, and
``Assets/crate.fbx`` made by Angelo), starts ``godot_link_e2e/angelo_side.py`` in a background Angelo, then the Unity
Editor in ``-batchmode -nographics`` running
``BatchTest.Run``: Edit in Angelo on the model, Angelo's edit and save written back as .fbx, the package's re-import on
every instance, a clean scene, Stop Live Link. Unity runs with the person's own HOME, so it finds the licence already
on this machine; it signs in to nothing. Files Unity wrote under ~/Library during the run are listed in the report.
Angelo's HOME, state and the link record are private to the run. Standard library only.
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
import run as harness  # noqa: E402  (MAKE_ASSETS, PACKAGE, ANGELO_SIDE)

SECONDS = 300
LIBRARY = ("Library/Unity", "Library/Logs/Unity", "Library/Application Support/Unity", "Library/Caches/com.unity3d.UnityEditor",
           "Library/Preferences")


def written_since(home, start):
    """Files under the person's ~/Library folders Unity uses that changed since ``start`` (Preferences: Unity's only)."""
    found = []
    for relative in LIBRARY:
        folder = Path(home, relative)
        if not folder.is_dir():
            continue
        paths = folder.glob("com.unity3d.*") if relative.endswith("Preferences") else folder.rglob("*")
        for path in paths:
            try:
                if path.is_file() and path.stat().st_mtime >= start:
                    found.append(str(path.relative_to(home)))
            except OSError:
                continue
    return sorted(found)


def make_project(out, angelo, env, unity_version):
    project = out / "project"
    (project / "Assets/Editor").mkdir(parents=True)
    (project / "Packages").mkdir()
    (project / "ProjectSettings").mkdir()   # An existing project: a new one would get Unity's default manifest.
    (project / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: %s\n" % unity_version,
                                                                encoding="utf-8")
    # Only modules built into the editor: an empty manifest reads as a new project, and Unity adds its default
    # registry packages to it.
    modules = ("imgui", "jsonserialize", "ui", "uielements", "physics")
    (project / "Packages/manifest.json").write_text(json.dumps({"dependencies": {
        "com.unity.modules." + name: "1.0.0" for name in modules}}, indent=1) + "\n", encoding="utf-8")
    shutil.copytree(harness.PACKAGE, project / "Packages/com.hyperbond.angelo-link")
    for name in ("BatchTest.cs", "AngeloLinkBatchTest.asmdef"):
        shutil.copy2(HERE / "editor" / name, project / "Assets/Editor" / name)
    subprocess.run([angelo, "--background", "--factory-startup", "--python-expr", harness.MAKE_ASSETS, "--",
                    str(project / "Assets")], check=True, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=120)
    (project / "Assets/crate.glb").unlink()   # glTFast isn't installed: Unity wouldn't import it as a model.
    return project


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--unity", required=True, help="a Unity 6 Unity.app")
    parser.add_argument("--binary", required=True, help="the Angelo to link with (background only)")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    home = out / "angelo-home"
    home.mkdir()
    record = out / "link-record.json"
    angelo_env = {key: os.environ[key] for key in ("PATH", "LANG", "TMPDIR", "USER", "LOGNAME", "SHELL")
                  if key in os.environ}
    angelo_env.update(HOME=str(home), CFFIXED_USER_HOME=str(home), ANGELO_STATE_DIR=str(home / "angelo-state"),
                      ANGELO_DISABLE_KEYCHAIN="1", ANGELO_DISABLE_INTRO="1", ANGELO_AGENT_RUNTIME="off",
                      ANGELO_LINK_RECORD=str(record), ANGELO_QA_DIR=str(out),
                      ANGELO_E2E_SECONDS=str(SECONDS))
    project = make_project(out, args.binary, angelo_env, Path(args.unity).parent.name)
    report_path = out / "unity.json"
    unity_env = dict(os.environ, ANGELO_LINK_RECORD=str(record), ANGELO_UNITY_REPORT=str(report_path),
                     ANGELO_UNITY_ASSET="Assets/crate.fbx", ANGELO_UNITY_SECONDS=str(SECONDS - 30),
                     ANGELO_UNITY_EXPORT=str(out / "export.txt"))
    started = time.time()
    with (out / "angelo.log").open("w") as log:
        side = subprocess.Popen([args.binary, "--background", "--factory-startup", "--python-exit-code", "1",
                                 "--python", str(harness.ANGELO_SIDE)], env=angelo_env, stdout=log,
                                stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 60
            while not record.exists() and side.poll() is None and time.monotonic() < deadline:
                time.sleep(0.1)
            unity = subprocess.run([str(Path(args.unity, "Contents/MacOS/Unity")), "-batchmode", "-nographics",
                                    "-projectPath", str(project), "-logFile", str(out / "unity.log"),
                                    "-executeMethod", "AngeloLinkTests.BatchTest.Run"],
                                   env=unity_env, timeout=SECONDS + 300, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
            try:
                angelo_code = side.wait(timeout=60)
            except subprocess.TimeoutExpired:
                angelo_code = "timeout"
        finally:
            if side.poll() is None:
                side.kill()
    unity_report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {"missing": True}
    angelo_path = out / "angelo.json"
    angelo_report = json.loads(angelo_path.read_text(encoding="utf-8")) if angelo_path.exists() else {"missing": True}
    log = (out / "unity.log").read_text(encoding="utf-8", errors="replace") if (out / "unity.log").exists() else ""
    licence_lines = [line.strip() for line in log.splitlines() if "licens" in line.lower()][:12]
    written = (project / "Assets/crate.fbx").read_bytes()
    report = {"unity_exit": unity.returncode, "angelo_exit": angelo_code, "unity": unity_report,
              "angelo_phase": angelo_report.get("phase"), "angelo_checks": angelo_report.get("checks"),
              "angelo_error": angelo_report.get("error") or angelo_report.get("traceback"),
              "edit_written_back": b"AngeloAdded" in written,
              "compile_errors": [line for line in log.splitlines() if ": error CS" in line][:10],
              "licence_log": licence_lines,
              "registry_packages": [line.strip() for line in log.splitlines() if "packages.unity.com" in line][:5],
              "written_under_library": written_since(Path.home(), started)}
    report["success"] = (unity.returncode == 0 and angelo_code == 0 and bool(unity_report.get("ok"))
                         and report["edit_written_back"])
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(("PASS" if report["success"] else "FAIL"), "report:", out / "report.json")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
