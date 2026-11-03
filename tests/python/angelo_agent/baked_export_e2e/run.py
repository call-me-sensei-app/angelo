# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""End-to-end proof that a baked texture set arrives as a working material in each engine
(doc/angelo/long_jobs.rst, "Baked materials in each engine"). Standard library only.

    python3 tests/python/angelo_agent/baked_export_e2e/run.py --binary <Angelo> --out DIR \\
        [--godot <Godot binary> ...] [--unity <Unity.app> --gltfast <PackageCache folder> [--urp]] \\
        [--unreal "/Users/Shared/Epic Games/UE_5.8"] [--browser <Chrome>]

1. ``make_assets.py`` in a background Angelo: bakes the DEV and HELD_OUT targets (GLTF packing), or with ``--kit``
   each mesh of held-out .glb models from its own materials, adds an unbound twin sharing the first target's mesh, and writes ``link.glb`` through the engine link's background write-back and
   ``web.glb`` through the web export. Angelo's own exporters and writers, nothing else.
2. Each engine imports the file with its own importer and reports each mesh's material and maps:
   Godot (``godot_check.gd``; ``--import`` then the script, and a windowed render), Unity with glTFast (a throwaway
   project embedding the glTFast package and its dependencies copied from ``--gltfast``, never downloaded;
   Built-in, and URP with ``--urp``; ``UnityBakedCheck.cs`` in batch mode with a render), Unreal (``unreal_check.py``
   through Interchange in a headless editor, plus the FBX axis calibration), three.js (``three_check.html`` with the
   web preview's bundled build in a headless browser, with a screenshot).
3. ``summary.json``: per engine, each baked target must carry base colour, metallic-roughness, occlusion from the same
   ORM image, and a normal map; the twin must keep its own material.
"""

import argparse
import functools
import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
FORWARDS = ("Y", "-Y", "X", "-X")   # Candidate FBX forward axes for the Unreal calibration (up is Z).

MAKE_AXES = """
import json, sys
import bpy
sys.path.insert(0, %r)
from angelo_agent.unreal_link_assets import UNREAL_FBX
from angelo_agent.engine_link_assets import GLTF_DURABLE
out = sys.argv[-1]
bpy.ops.wm.read_factory_settings(use_empty=True)
mesh = bpy.data.meshes.new("Axes")   # Asymmetric: 2 m along +X, 0.5 m along +Y, 1 m along +Z, all from the origin.
mesh.from_pydata([(x, y, z) for x in (0, 2) for y in (0, 0.5) for z in (0, 1)], [],
                 [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)])
obj = bpy.data.objects.new("Axes", mesh)
bpy.context.scene.collection.objects.link(obj)
bpy.ops.export_scene.gltf(filepath=out + "/axes.glb", export_format="GLB", **GLTF_DURABLE)
for forward in %r:
    bpy.ops.export_scene.fbx(filepath="%%s/axes_%%s.fbx" %% (out, forward.replace("-", "neg")),
                             **dict(UNREAL_FBX, axis_forward=forward))
"""

UPROJECT = {"FileVersion": 3, "EngineAssociation": "5.8", "Category": "", "Description": "Angelo baked check",
            "Plugins": [{"Name": "PythonScriptPlugin", "Enabled": True}, {"Name": "Interchange", "Enabled": True}]}


def log_run(command, log, env=None, timeout=1800, cwd=None):
    with open(log, "w") as handle:
        try:
            return subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT, env=env, timeout=timeout,
                                  cwd=cwd).returncode
        except subprocess.TimeoutExpired:
            return "timeout"


def make_assets(args, out):
    assets = out / "assets"
    code = log_run([args.binary, "--background", "--factory-startup", "--python-exit-code", "1",
                    "--python", str(HERE / "make_assets.py"), "--", str(assets)] + [str(Path(kit).resolve())
                                                                                   for kit in args.kit],
                   out / "angelo.log", timeout=900)
    if code != 0:
        raise SystemExit("make_assets failed (%s); see %s" % (code, out / "angelo.log"))
    return assets, json.loads((assets / "truth.json").read_text())


def key(name):
    """A name as engines rewrite it compared loosely: lower case, letters and digits only, without the prefixes
    Unreal adds (``SM_``, ``MI_``) or the suffixes Unity and Unreal add (`` (Instance)``, ``.``-paths)."""
    name = str(name or "").split(" (")[0].split(".")[0]
    for prefix in ("SM_", "MI_"):
        name = name[len(prefix):] if name.startswith(prefix) else name
    return "".join(ch for ch in name.lower() if ch.isalnum())


def judge(truth, objects, has_maps, material_name):
    """Per object: True when it carries what it should (``has_maps(material, normal)`` and its baked material for a
    baked target, ``normal`` saying whether its set has a normal map; its own material for the twin). Names are
    compared by ``key``."""
    objects = {key(name): value for name, value in objects.items()}
    verdict = {}
    for name, baked in truth["baked"].items():
        found = objects.get(key(name)) or []
        normal = "NORMAL" in truth.get("maps", {}).get(name, ["NORMAL"])
        verdict[name] = bool(found) and all(item and has_maps(item, normal) and key(material_name(item)) == key(baked)
                                            for item in found)
    for name, own in truth["own"].items():
        found = objects.get(key(name)) or []
        verdict[name] = bool(found) and all(item and key(material_name(item)) == key(own) for item in found)
    return verdict


def godot(args, out, assets, truth, binary, index):
    project = out / ("godot-%d" % index)
    project.mkdir()
    (project / "project.godot").write_text("config_version=5\n\n[application]\n\nconfig/name=\"AngeloBakedCheck\"\n")
    shutil.copy2(assets / "link.glb", project / "link.glb")
    shutil.copy2(HERE / "godot_check.gd", project / "godot_check.gd")
    log_run([binary, "--headless", "--path", str(project), "--import"], out / ("godot-%d-import.log" % index))
    report = out / ("godot-%d.json" % index)
    env = dict(os.environ, ANGELO_CHECK_OUT=str(report))
    log_run([binary, "--headless", "--path", str(project), "--script", "res://godot_check.gd"],
            out / ("godot-%d-check.log" % index), env=env, timeout=300)
    picture = out / ("godot-%d.png" % index)
    log_run([binary, "--path", str(project), "--resolution", "960x540", "--script", "res://godot_check.gd"],
            out / ("godot-%d-render.log" % index), env=dict(env, ANGELO_CHECK_OUT=str(out / "godot-render.json"),
                                                            ANGELO_CHECK_PNG=str(picture)), timeout=300)
    found = json.loads(report.read_text()) if report.exists() else {"objects": {}}
    has = lambda m, normal: (m["albedo"] and m["roughness"] and m["metallic"] and m["ao"] and m["normal"] == normal
                             and m["ao_shares_orm"]
                             and (m["ao_channel"], m["roughness_channel"], m["metallic_channel"]) == (0, 1, 2))
    return {"engine": found.get("engine"), "report": found, "picture": picture.name if picture.exists() else None,
            "verdict": judge(truth, found.get("objects", {}), has, lambda m: m["name"])}


def unity(args, out, assets, truth, pipeline):
    project = out / ("unity-%s" % pipeline)
    (project / "Assets/Editor").mkdir(parents=True)
    packages = project / "Packages"
    packages.mkdir()
    dependencies = {"com.unity.modules.jsonserialize": "1.0.0", "com.unity.modules.unitywebrequest": "1.0.0",
                    "com.unity.modules.imageconversion": "1.0.0"}
    builtin = Path(args.unity, "Contents/Resources/PackageManager/BuiltInPackages")
    for cached in sorted(Path(args.gltfast).iterdir()):   # Embedded: copied from a local package cache, never fetched.
        if (cached / "package.json").exists():
            name = json.loads((cached / "package.json").read_text())["name"]
            # glTFast and the dependencies this editor doesn't ship itself (Unity 6.5 builds in Mathematics and
            # Collections; an embedded copy of either conflicts with the engine's own).
            if name.startswith(("com.unity.cloud.gltfast", "com.unity.burst")) and not (builtin / name).exists():
                shutil.copytree(cached, packages / name)
    if pipeline == "urp":
        urp = builtin / "com.unity.render-pipelines.universal"
        dependencies["com.unity.render-pipelines.universal"] = json.loads((urp / "package.json").read_text())["version"]
    (packages / "manifest.json").write_text(json.dumps({"dependencies": dependencies}, indent=2))
    shutil.copy2(HERE / "UnityBakedCheck.cs", project / "Assets/Editor/UnityBakedCheck.cs")
    editor = str(Path(args.unity, "Contents/MacOS/Unity"))
    base = [editor, "-batchmode", "-projectPath", str(project), "-accept-apiupdate"]
    report, picture = out / ("unity-%s.json" % pipeline), out / ("unity-%s.png" % pipeline)
    env = dict(os.environ, ANGELO_CHECK_OUT=str(report), ANGELO_CHECK_PNG=str(picture))
    if pipeline == "urp":
        log_run(base + ["-executeMethod", "AngeloBakedCheck.UseUrp", "-logFile", str(out / "unity-urp-setup.log")],
                out / "unity-urp-setup.out", env=env)
    shutil.copy2(assets / "link.glb", project / "Assets/link.glb")
    log_run(base + ["-executeMethod", "AngeloBakedCheck.Run", "-logFile", str(out / ("unity-%s.log" % pipeline))],
            out / ("unity-%s.out" % pipeline), env=env)
    found = json.loads(report.read_text()) if report.exists() else {"objects": {}}
    has = lambda m, normal: all(m.get(key) for key in ("baseColorTexture", "metallicRoughnessTexture",
                                                       "occlusionTexture", "orm_shared")) and m["normalTexture"] == normal
    return {"unity": found.get("unity"), "pipeline": found.get("pipeline"), "report": found,
            "picture": picture.name if picture.exists() else None,
            "verdict": judge(truth, found.get("objects", {}), has, lambda m: m["name"])}


def unreal(args, out, assets, truth):
    work = Path(args.work, time.strftime("%H%M%S")).resolve()
    project = work / "Proj"
    (project / "Content/Python").mkdir(parents=True)
    (project / "Check.uproject").write_text(json.dumps(UPROJECT, indent=2))
    shutil.copy2(HERE / "unreal_check.py", project / "Content/Python/init_unreal.py")
    folder = work / "assets"
    folder.mkdir()
    shutil.copy2(assets / "link.glb", folder / "link.glb")
    log_run([args.binary, "--background", "--factory-startup", "--python-expr", MAKE_AXES % (str(ADDON.parent),
             FORWARDS), "--", str(folder)], out / "unreal-axes-angelo.log", timeout=300)
    home = work / "home"
    home.mkdir()
    report = out / "unreal.json"
    env = dict(os.environ, HOME=str(home), CFFIXED_USER_HOME=str(home), ANGELO_UNREAL_REPORT=str(report),
               ANGELO_UNREAL_ASSETS=str(folder))
    env["UE-LocalDataCachePath"] = str(work / "ddc")
    env["UE-SharedDataCachePath"] = "None"
    editor = Path(args.unreal, "Engine/Binaries/Mac/UnrealEditor.app/Contents/MacOS/UnrealEditor")
    try:
        log_run([str(editor), str(project / "Check.uproject"), "-nullrhi", "-unattended", "-nocrashreports",
                 "-nosplash", "-nopause", "-nosound", "-NoLiveCoding", "-log", "-abslog=" + str(out / "unreal.log")],
                out / "unreal.out", env=env, timeout=args.seconds)
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
    found = json.loads(report.read_text()) if report.exists() else {"meshes": {}}

    def has(material, normal):
        textures = {name: value for name, value in (material.get("textures") or {}).items() if value}
        normals = [value for name, value in textures.items() if "normal" in name.lower()]
        orm = textures.get("OcclusionTexture", {}).get("texture")
        return (material.get("class") == "MaterialInstanceConstant" and "BaseColorTexture" in textures
                and orm is not None and orm == textures.get("MetallicRoughnessTexture", {}).get("texture")
                and bool(normals) == normal and all(value["flip_green_channel"] for value in normals))

    meshes = dict(found.get("meshes", {}))
    # A skeletal mesh holds the parts of a rigged model as material slots: each baked part is judged by the slot
    # holding its "<object> Baked" material (Angelo's name for it).
    for slots in found.get("skeletal", {}).values():
        for name, baked in truth["baked"].items():
            meshes.setdefault(name, []).extend(slot for slot in slots if slot and key(slot["name"]) == key(baked))
    axes = found.get("axes", {})
    glb = axes.get("axes.glb")
    agree = [name for name, boxes in axes.items() if name.endswith(".fbx") and boxes == glb]
    return {"engine": found.get("engine"), "report": found, "fbx_forward_matching_glb": agree,
            "verdict": judge(truth, meshes, has, lambda m: m["name"])}


def three(args, out, assets, truth):
    site = out / "three-site"
    site.mkdir()
    shutil.copytree(ADDON / "web_preview/vendor", site / "vendor")
    shutil.copy2(HERE / "three_check.html", site / "index.html")
    shutil.copy2(assets / "web.glb", site / "web.glb")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(site))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/index.html" % server.server_address[1]
    profile = out / "chrome-profile"
    flags = [args.browser, "--headless=new", "--user-data-dir=" + str(profile), "--use-mock-keychain",
             "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--window-size=960,540",
             "--virtual-time-budget=20000", "--run-all-compositor-stages-before-draw"]
    try:
        dom = subprocess.run(flags + ["--dump-dom", url], capture_output=True, text=True, timeout=120).stdout
        subprocess.run(flags + ["--screenshot=" + str(out / "three.png"), url], capture_output=True, timeout=120)
    finally:
        server.shutdown()
        shutil.rmtree(profile, ignore_errors=True)
        shutil.rmtree(site, ignore_errors=True)
    start = dom.find('<pre id="result">')
    text = dom[start + len('<pre id="result">'):dom.find("</pre>", start)] if start >= 0 else '{"error": "no page"}'
    found = json.loads(text.replace("&quot;", '"').replace("&amp;", "&")) if text.startswith("{") else {"error": text}
    (out / "three.json").write_text(json.dumps(found, indent=2))
    has = lambda m, normal: all(m[key] for key in ("map", "roughnessMap", "metalnessMap", "aoMap", "orm_shared")) \
        and m["normalMap"] == normal
    return {"three": found.get("three"), "report": found, "picture": "three.png",
            "verdict": judge(truth, found.get("objects", {}), has, lambda m: m["name"])}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--kit", action="append", default=[], help="held-out .glb models baked from their own "
                        "materials instead of the suite's targets")
    parser.add_argument("--godot", action="append", default=[])
    parser.add_argument("--unity")
    parser.add_argument("--gltfast", help="a local Unity PackageCache folder holding glTFast and its dependencies")
    parser.add_argument("--urp", action="store_true")
    parser.add_argument("--unreal")
    parser.add_argument("--browser")
    parser.add_argument("--seconds", type=int, default=1800)
    parser.add_argument("--work", default=str(Path.home() / "angelo-ue.noindex"))
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    assets, truth = make_assets(args, out)
    summary = {"truth": truth, "engines": {}}
    for index, binary in enumerate(args.godot):
        summary["engines"]["godot-%d" % index] = godot(args, out, assets, truth, binary, index)
    if args.unity:
        for pipeline in ("builtin", "urp") if args.urp else ("builtin",):
            summary["engines"]["unity-" + pipeline] = unity(args, out, assets, truth, pipeline)
    if args.unreal:
        summary["engines"]["unreal"] = unreal(args, out, assets, truth)
    if args.browser:
        summary["engines"]["three"] = three(args, out, assets, truth)
    for result in summary["engines"].values():
        result["ok"] = bool(result["verdict"]) and all(result["verdict"].values())
    summary["ok"] = bool(summary["engines"]) and all(result["ok"] for result in summary["engines"].values())
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps({name: (result["ok"], result["verdict"]) for name, result in summary["engines"].items()}, indent=1))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
