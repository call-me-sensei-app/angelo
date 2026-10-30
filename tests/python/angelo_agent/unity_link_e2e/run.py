# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Checks of the Unity Angelo Link package that need no Unity licence (doc/angelo/unity_link.rst, Validation).

    python3 tests/python/angelo_agent/unity_link_e2e/run.py --unity /Applications/Unity/Unity-6000.3.13f1/Unity.app \\
        --out DIR [--binary /abs/Angelo.app/Contents/MacOS/Angelo]

1. **Compiles** the package's Editor assembly against that Unity's own UnityEngine/UnityEditor assemblies with its
   bundled Roslyn (nothing runs Unity, so no licence or sign-in is involved).
2. **Builds the protocol harness** (``Harness.cs`` here) with the package's engine-free files and runs it on Unity's
   bundled .NET runtime: proofs against Angelo's own ``engine_link_protocol`` for the same token and nonces, fake
   servers (a wrong server proof is refused, frames split across reads are joined).
3. With ``--binary``, **a real background Angelo** (``godot_link_e2e/angelo_side.py``) and a throwaway Unity project
   (``Assets/``, ``ProjectSettings/ProjectVersion.txt``) holding ``Assets/crate.glb`` and ``Assets/crate.fbx``: for
   each, the harness asks to open it as ``engine: "unity"``, and Angelo's Unity adapter takes it through open, two
   scenes, saved, "reimported" and close; the model on disk then holds Angelo's edit, written back with the kind's
   exporter, and the working .blend sits outside ``Assets``. HOME, Angelo's state and the record are private to the
   run (doc/angelo/unity_link.rst, "Validation"). Standard library only.
"""

import argparse
import glob
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
PACKAGE = ROOT / "scripts/addons_core/angelo_agent/unity_package/com.hyperbond.angelo-link"
ADDON = ROOT / "scripts/addons_core/angelo_agent"
ANGELO_SIDE = HERE.parent / "godot_link_e2e/angelo_side.py"
SECONDS = int(os.environ.get("ANGELO_UNITY_E2E_SECONDS", "150"))
ENGINE_FREE = ("MiniJson.cs", "LinkProtocol.cs", "LinkClient.cs")

MAKE_ASSETS = """
import bpy, sys
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_cube_add(size=2)
bpy.context.object.name = "Crate"
folder = sys.argv[-1]
bpy.ops.export_scene.gltf(filepath=folder + "/crate.glb", export_format="GLB")
bpy.ops.export_scene.fbx(filepath=folder + "/crate.fbx")
"""
MODELS = ("Assets/crate.glb", "Assets/crate.fbx")


def unity_tools(app):
    """(dotnet, csc.dll, netstandard.dll, Managed folder, the runtime's framework folder) of a Unity 6 install."""
    contents = Path(app) / "Contents"
    scripting = contents / "Resources/Scripting"
    if not (scripting / "Managed").is_dir():
        scripting = contents   # 6000.2 keeps them in Contents/
    if (scripting / "DotNetSdkRoslyn/csc.dll").is_file():
        dotnet, csc = scripting / "NetCoreRuntime/dotnet", scripting / "DotNetSdkRoslyn/csc.dll"
        runtime = "NetCoreRuntime"
    else:
        dotnet = scripting / "DotNetSdk/dotnet"
        csc = Path(sorted(glob.glob(str(scripting / "DotNetSdk/sdk/*/Roslyn/bincore/csc.dll")))[-1])
        runtime = "DotNetSdk"
    framework = Path(sorted(glob.glob(str(scripting / runtime / "shared/Microsoft.NETCore.App/*")))[-1])
    return dotnet, csc, scripting / "NetStandard/ref/2.1.0/netstandard.dll", scripting / "Managed", framework


def csc(tools, out, sources, references, defines=(), target="library"):
    dotnet, compiler = tools[0], tools[1]
    command = [str(dotnet), str(compiler), "-nologo", "-noconfig", "-nostdlib", "-langversion:9", "-warn:4",
               "-target:" + target, "-out:" + str(out)] + ["-r:" + str(path) for path in references]
    command += ["-define:" + name for name in defines] + [str(path) for path in sources]
    result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    return {"ok": result.returncode == 0, "output": (result.stdout + result.stderr).strip()[-4000:]}


def compile_editor(tools, out):
    managed = tools[3]
    references = [tools[2], managed / "UnityEngine.dll", managed / "UnityEditor.dll"]
    references += sorted(Path(managed, "UnityEngine").glob("*.dll"))
    return csc(tools, out / "Hyperbond.AngeloLink.Editor.dll", sorted(PACKAGE.glob("Editor/*.cs")), references)


def build_harness(tools, out):
    framework = tools[4]
    references = [path for path in sorted(framework.glob("*.dll")) if _managed(path)]
    sources = [PACKAGE / "Editor" / name for name in ENGINE_FREE] + [HERE / "Harness.cs"]
    target = out / "harness" / "Harness.dll"
    target.parent.mkdir(parents=True, exist_ok=True)
    result = csc(tools, target, sources, references, (), "exe")
    (target.parent / "Harness.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {
        "tfm": "net%s.0" % framework.name.split(".")[0],
        "framework": {"name": "Microsoft.NETCore.App", "version": framework.name}}}), encoding="utf-8")
    return target, result


def _managed(path):
    with open(path, "rb") as handle:
        return handle.read(2) == b"MZ"


def harness(tools, dll, *args, env=None, timeout=60):
    result = subprocess.run([str(tools[0]), str(dll)] + [str(arg) for arg in args], capture_output=True, text=True,
                            timeout=timeout, env=env)
    lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
    report = json.loads(lines[-1]) if lines else {"ok": False, "stderr": result.stderr[-2000:]}
    report["exit"] = result.returncode
    return report


def angelo_protocol():
    """Angelo's own protocol module, loaded by path (it imports nothing from Blender at module level)."""
    spec = importlib.util.spec_from_file_location("angelo_link_protocol", ADDON / "engine_link_protocol.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_vectors(tools, dll):
    token = bytes(range(32))
    server, client = "a1" * 32, "b2" * 32
    report = harness(tools, dll, "vector", token.hex(), server, client)
    module = angelo_protocol()
    return {"protocol": report.get("protocol"), "checks": {
        "harness_ran": report.get("exit") == 0,
        "protocol_is_angelos": report.get("protocol") == module.PROTOCOL,
        "client_proof_matches_angelo": report.get("client") == module.client_proof(token, server, client),
        "server_proof_matches_angelo": report.get("server") == module.server_proof(token, server, client)}}


def end_to_end(tools, out, angelo):
    """One linked session per model kind, each against its own background Angelo."""
    folder = out / "e2e"
    home, project = folder / "home", folder / "project"
    (project / "Assets").mkdir(parents=True)
    (project / "ProjectSettings").mkdir()
    (project / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 6000.5.6f1\n", encoding="utf-8")
    home.mkdir()
    env = {key: os.environ[key] for key in ("PATH", "LANG", "TMPDIR", "USER", "LOGNAME", "SHELL") if key in os.environ}
    env.update(HOME=str(home), CFFIXED_USER_HOME=str(home), ANGELO_STATE_DIR=str(home / "angelo-state"),
               ANGELO_DISABLE_KEYCHAIN="1", ANGELO_DISABLE_INTRO="1", ANGELO_AGENT_RUNTIME="off")
    subprocess.run([angelo, "--background", "--factory-startup", "--python-expr", MAKE_ASSETS, "--",
                    str(project / "Assets")], check=True, env=env, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=120)
    sessions = {model: session(tools, out, angelo, project, model, dict(env)) for model in MODELS}
    return {"ok": all(item["ok"] for item in sessions.values()), "sessions": sessions}


def session(tools, out, angelo, project, model, env):
    folder = out / "e2e" / Path(model).suffix[1:]
    folder.mkdir(parents=True)
    record = folder / "link-record.json"
    env.update(ANGELO_LINK_RECORD=str(record), ANGELO_QA_DIR=str(folder))
    with (folder / "angelo.log").open("w") as log:
        side = subprocess.Popen([angelo, "--background", "--factory-startup", "--python-exit-code", "1",
                                 "--python", str(ANGELO_SIDE)], env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 60
            while not record.exists() and side.poll() is None and time.monotonic() < deadline:
                time.sleep(0.1)
            time.sleep(0.2)   # The record is written exclusively in one write; let it land.
            protocol = json.loads(record.read_text(encoding="utf-8")).get("protocol") if record.exists() else None
            unity = harness(tools, out / "harness/Harness.dll", "e2e", project.resolve(), model, SECONDS, "unity",
                            env=env, timeout=SECONDS + 30)
            try:
                code = side.wait(timeout=60)
            except subprocess.TimeoutExpired:
                code = "timeout"
        finally:
            if side.poll() is None:
                side.kill()
    path = folder / "angelo.json"
    angelo_report = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"missing": True}
    written = (project / model).read_bytes()
    checks = {"session": bool(unity.get("ok")), "angelo_exit_0": code == 0,
              "edit_written_back": b"AngeloAdded" in written,
              "working_blend_outside_assets": (project / ".angelo" / (model + ".blend")).is_file(),
              "wire": protocol == "angelo-link/1"}
    return {"ok": all(checks.values()), "checks": checks, "harness": unity, "angelo_exit": code,
            "angelo_checks": angelo_report.get("checks"), "angelo_phase": angelo_report.get("phase"),
            "versions_sent": angelo_report.get("versions_sent"), "last_export_ms": angelo_report.get("last_export_ms"),
            "angelo_error": angelo_report.get("error") or angelo_report.get("traceback")}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--unity", required=True, help="a Unity 6 Unity.app (its assemblies and compiler only)")
    parser.add_argument("--out", required=True)
    parser.add_argument("--binary", help="an Angelo to run the end-to-end session against (background only)")
    args = parser.parse_args(argv)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    tools = unity_tools(args.unity)
    report = {"unity": args.unity, "compile_editor": compile_editor(tools, out)}
    dll, built = build_harness(tools, out)
    report["wire"] = {"build": built}
    if built["ok"]:
        report["wire"]["vectors"] = check_vectors(tools, dll)
        report["wire"]["offline"] = harness(tools, dll, "offline")
    if args.binary:
        report["end_to_end"] = end_to_end(tools, out, args.binary)
    flags = [report["compile_editor"]["ok"]]
    part = report["wire"]
    flags += [part["build"]["ok"], part.get("offline", {}).get("ok", False)]
    flags += list(part.get("vectors", {}).get("checks", {"missing": False}).values())
    if "end_to_end" in report:
        flags.append(report["end_to_end"]["ok"])
    report["success"] = all(flags)
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(("PASS" if report["success"] else "FAIL"), "report:", out / "report.json")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
