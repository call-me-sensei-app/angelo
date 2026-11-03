# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine-skeleton check against real engine imports (doc/angelo/engine_skeleton.rst, "Engine proof").

    python3 tests/python/angelo_agent/skeleton_engine_e2e/run.py --binary <Angelo> --out DIR \\
        [--godot <Godot binary>] [--unity <Unity.app>] [--unreal <UE install folder>]

1. Angelo builds the rigs (``make_rigs.py``), records its verdict per rig and engine FIRST, and exports each the way
   that engine's usual import reads it.
2. Each engine given imports them with its defaults:
   * **Godot** (headless): its own ``SkeletonProfileHumanoid`` read back as data (compared with Angelo's profile),
     then each .glb re-imported with a BoneMap built from Angelo's roles; Godot's skeleton afterwards must hold every
     bone its profile requires, under the profile's parent.
   * **Unity** (batchmode, the machine's own licence, as unity_link_e2e): each .fbx imported as a Humanoid; Unity's
     automap makes the avatar, and ``Avatar.isValid`` and ``isHuman`` say whether it took. Unity's required slots are
     read from ``HumanTrait``.
   * **Unreal** (``-nullrhi -unattended``, as unreal_link_e2e): each .glb and .fbx imported as a skeletal mesh, its
     bone names read back, and an IK Rig's Auto Create Retarget Chains run: whether a template matched. UE 5.8.2
     crashes on exit and leaves a CrashReportClient spinning; the run stops it by PID (only one writing under its own
     work folder) and records the PIDs in ``crash_reporters_stopped``.
   ``--extra FILE...`` adds held-out rig files to Unreal's imports (``make_rigs.py``).
3. ``summary.json``: each engine's result beside Angelo's prediction; ``counts`` keeps Unreal's uncertain predictions
   apart from the certain ones that agree or disagree. Standard library only.
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
sys.path.insert(0, str(ROOT / "scripts/addons_core"))


def angelo_env(out):
    home = out / "angelo-home"
    home.mkdir(exist_ok=True)
    base = {key: os.environ[key] for key in ("PATH", "LANG", "TMPDIR", "USER", "LOGNAME", "SHELL") if key in os.environ}
    return dict(base, HOME=str(home), CFFIXED_USER_HOME=str(home), ANGELO_STATE_DIR=str(home / "angelo-state"),
                ANGELO_DISABLE_KEYCHAIN="1", ANGELO_DISABLE_INTRO="1", ANGELO_AGENT_RUNTIME="off",
                ANGELO_DISABLE_DEFAULT_CHAT="1")


def make_rigs(binary, out, extra=()):
    with (out / "make_rigs.log").open("w") as log:
        subprocess.run([binary, "--background", "--factory-startup", "--python-exit-code", "1", "--python",
                        str(HERE / "make_rigs.py"), "--", str(out), "--extra"] + [str(Path(e).resolve()) for e in extra],
                       check=True, env=angelo_env(out), stdout=log, stderr=subprocess.STDOUT, timeout=1200)
    return json.loads((out / "angelo.json").read_text(encoding="utf-8"))


# --- Godot -----------------------------------------------------------------------------------------------------------

def godot(binary, out, angelo):
    project = out / "godot"
    project.mkdir()
    (project / "project.godot").write_text('config_version=5\n\n[application]\n\nconfig/name="skeleton"\n',
                                           encoding="utf-8")
    roles = {}
    for rig, verdicts in angelo.items():
        shutil.copy2(out / "rigs" / ("%s.godot.glb" % rig), project / ("%s.glb" % rig))
        roles[rig] = verdicts["GODOT/GLB"]["roles"]
    (out / "godot-roles.json").write_text(json.dumps(roles), encoding="utf-8")
    log = (out / "godot.log").open("w")

    def run(*args, phase=None):
        env = dict(os.environ, ANGELO_GODOT_ROLES=str(out / "godot-roles.json"),
                   ANGELO_GODOT_REPORT=str(out / ("godot-%s.json" % phase)), ANGELO_GODOT_PHASE=phase or "")
        subprocess.run([binary, "--headless", "--path", str(project)] + list(args), env=env, stdout=log,
                       stderr=subprocess.STDOUT, timeout=600)

    run("--import")
    run("-s", str(HERE / "godot_check.gd"), phase="map")
    mapped = json.loads((out / "godot-map.json").read_text(encoding="utf-8"))
    for rig, entry in mapped["rigs"].items():
        if not entry.get("skeleton"):
            continue
        settings = project / ("%s.glb.import" % rig)
        text = settings.read_text(encoding="utf-8")
        nodes = ('_subresources={\n"nodes": {\n"PATH:%s": {\n"retarget/bone_map": Resource("res://maps/%s.tres")\n}'
                 '\n}\n}' % (entry["path"], rig))
        settings.write_text(text.replace("_subresources={}", nodes), encoding="utf-8")
    run("--import")
    run("-s", str(HERE / "godot_check.gd"), phase="read")
    log.close()
    read = json.loads((out / "godot-read.json").read_text(encoding="utf-8"))
    return grade_godot(mapped["profile"], read, angelo)


def grade_godot(profile, read, angelo):
    from angelo_agent import skeleton_profiles as P
    godot_bones = {b["name"]: b for b in profile["bones"]}
    data = {"names_and_parents_match": all(
                godot_bones.get(role, {}).get("parent", "") == (parent or "") for role, parent in P.ROLES)
            and len(godot_bones) == len(P.ROLES),
            "required_match": sorted(b["name"] for b in profile["bones"] if b["required"]) == sorted(P.GODOT_REQUIRED),
            "godot_required": sorted(b["name"] for b in profile["bones"] if b["required"])}
    rigs = {}
    for rig, entry in read["rigs"].items():
        names = {b["name"]: b["parent"] for b in entry.get("bones", ())}
        missing = [b["name"] for b in profile["bones"] if b["required"] and b["name"] not in names]
        misparented = []
        for bone in profile["bones"]:
            if bone["name"] not in names or not bone["parent"] or bone["parent"] not in names:
                continue
            above, seen = names[bone["name"]], 0   # The nearest profile bone above it must be the profile's parent.
            while above and above not in godot_bones and seen < 1024:
                above, seen = names.get(above, ""), seen + 1
            if above != bone["parent"]:   # another profile bone, or none at all (a chain through control bones)
                misparented.append(bone["name"])
        accepted = not missing and not misparented
        predicted = angelo[rig]["GODOT/GLB"]["statuses"].get("bone_match") in ("pass", "info")
        rigs[rig] = {"godot_accepts": accepted, "missing_required": missing, "misparented": misparented,
                     "angelo_predicts": predicted, "agree": accepted == predicted}
    return {"engine": read.get("engine"), "profile": data, "rigs": rigs}


# --- Unity -----------------------------------------------------------------------------------------------------------

def unity(app, out, angelo):
    project = out / "unity"
    for folder in ("Assets/Editor", "Assets/Rigs", "Packages", "ProjectSettings"):
        (project / folder).mkdir(parents=True)
    (project / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: %s\n" % Path(app).parent.name,
                                                                encoding="utf-8")
    modules = ("imgui", "jsonserialize", "ui", "uielements", "physics", "animation")
    (project / "Packages/manifest.json").write_text(json.dumps({"dependencies": {
        "com.unity.modules." + name: "1.0.0" for name in modules}}, indent=1) + "\n", encoding="utf-8")
    for name in ("SkeletonCheck.cs", "AngeloSkeletonTests.asmdef"):
        shutil.copy2(HERE / "unity" / name, project / "Assets/Editor" / name)
    for rig in angelo:
        shutil.copy2(out / "rigs" / ("%s.unity.fbx" % rig), project / "Assets/Rigs" / ("%s.fbx" % rig))
    report = out / "unity.json"
    subprocess.run([str(Path(app, "Contents/MacOS/Unity")), "-batchmode", "-nographics", "-projectPath",
                    str(project), "-logFile", str(out / "unity.log"), "-executeMethod",
                    "AngeloSkeletonTests.SkeletonCheck.Run"], env=dict(os.environ, ANGELO_UNITY_REPORT=str(report)),
                   timeout=1200, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    result = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {"missing": True}
    log = (out / "unity.log").read_text(encoding="utf-8", errors="replace") if (out / "unity.log").exists() else ""
    from angelo_agent import skeleton_profiles as P
    rigs = {}
    for rig in angelo:
        entry = result.get("rigs", {}).get(rig + ".fbx", {})
        accepted = bool(entry.get("valid") and entry.get("human"))
        predicted = angelo[rig]["UNITY/FBX"]["statuses"].get("bone_match") == "pass"
        rigs[rig] = {"unity_accepts": accepted, "mapped": len(entry.get("mapped", {})), "angelo_predicts": predicted,
                     "agree": accepted == predicted}
    return {"engine": result.get("unity"), "required": result.get("required"),
            "required_match": sorted(result.get("required") or []) == sorted(P.UNITY.names[r] for r in P.UNITY_REQUIRED),
            "rigs": rigs, "avatar_log": [line.strip() for line in log.splitlines() if "vatar" in line][:20]}


# --- Unreal ----------------------------------------------------------------------------------------------------------

UPROJECT = {"FileVersion": 3, "EngineAssociation": "5.8", "Category": "", "Description": "Angelo skeleton test",
            "Plugins": [{"Name": "PythonScriptPlugin", "Enabled": True}, {"Name": "Interchange", "Enabled": True},
                        {"Name": "IKRig", "Enabled": True}, {"Name": "MeshModelingToolset", "Enabled": True}]}


def crash_reporters(folder, listing=None):
    """PIDs of Unreal crash reporters writing a report under ``folder`` (this run's own HOME). The editor crashes on
    exit in UE 5.8.2 after the imports finish and, despite ``-nocrashreports``, leaves a CrashReportClient that
    launchd adopts and that spins at 100% CPU. Matched by this run's folder, so another Unreal's are never touched."""
    if listing is None:
        listing = subprocess.run(["ps", "axo", "pid=,command="], capture_output=True, text=True).stdout
    marker = str(folder) + os.sep
    found = []
    for line in listing.splitlines():
        pid, _, command = line.strip().partition(" ")
        if pid.isdigit() and "CrashReportClient" in command.split(marker)[0] and marker in command:
            found.append(int(pid))
    return found


def stop_crash_reporters(folder, wait=15.0):
    """Stops this run's crash reporters by PID once the editor has gone, waiting up to ``wait`` seconds for one to
    start (it starts as the editor dies). Returns the PIDs stopped."""
    stopped, deadline = [], time.monotonic() + wait
    while True:
        for pid in crash_reporters(folder):
            try:
                os.kill(pid, 15)
                stopped.append(pid)
            except ProcessLookupError:
                pass
        if stopped or time.monotonic() > deadline:
            return stopped
        time.sleep(1.0)


def unreal(engine, out, angelo, work):
    project = work / "Skel"
    (project / "Content/Python").mkdir(parents=True)
    (project / "Skel.uproject").write_text(json.dumps(UPROJECT, indent=1), encoding="utf-8")
    shutil.copy2(HERE / "unreal_check.py", project / "Content/Python/init_unreal.py")
    rigs = work / "rigs"
    rigs.mkdir()
    extra = json.loads((out / "angelo-extra.json").read_text(encoding="utf-8")) if (out / "angelo-extra.json").exists() else {}
    for rig in list(angelo) + list(extra):
        for fmt in ("glb", "fbx"):
            shutil.copy2(out / "rigs" / ("%s.unreal.%s" % (rig, fmt)), rigs / ("%s.%s" % (rig, fmt)))
    home = work / "h"
    home.mkdir()
    base = {key: os.environ[key] for key in ("PATH", "LANG", "TMPDIR", "USER", "LOGNAME", "SHELL") if key in os.environ}
    report = out / "unreal.json"
    env = dict(base, HOME=str(home), CFFIXED_USER_HOME=str(home), ANGELO_UNREAL_RIGS=str(rigs),
               ANGELO_UNREAL_REPORT=str(report))
    env["UE-LocalDataCachePath"] = str(work / "ddc")
    env["UE-SharedDataCachePath"] = "None"
    editor = Path(engine, "Engine/Binaries/Mac/UnrealEditor.app/Contents/MacOS/UnrealEditor")
    command = [str(editor), str(project / "Skel.uproject"), "-nullrhi", "-unattended", "-nocrashreports",
               "-nosplash", "-nopause", "-nosound", "-NoLiveCoding", "-log", "-abslog=" + str(work / "unreal.log")]
    try:
        code = subprocess.run(command, env=env, timeout=2400, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode
    except subprocess.TimeoutExpired:
        code = "timeout"
    reporters = stop_crash_reporters(home)
    if (work / "unreal.log").exists():
        shutil.copy2(work / "unreal.log", out / "unreal.log")
    result = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {"missing": True}
    graded = {}
    for file, entry in result.get("rigs", {}).items():
        rig, fmt = file.rsplit(".", 1)
        applied = bool(entry.get("retarget", {}).get("applied"))
        # Angelo's prediction is "recognised" or uncertain ("uncertain", "unmatched"); an uncertain one never counts
        # as agreeing, whatever Unreal did.
        verdicts = angelo.get(rig) or extra[rig]
        predicted = verdicts["UNREAL/%s" % fmt.upper()].get("predictions", {}).get("auto_retarget")
        graded[file] = {"unreal_applies": applied, "pelvis": entry.get("retarget", {}).get("pelvis"),
                        "chains": len(entry.get("retarget", {}).get("chains", ())), "angelo_predicts": predicted,
                        "agree": None if predicted != "recognised" else applied, "error": entry.get("error"),
                        "held_out": rig in extra,
                        "bone_names_sample": [b["name"] for b in entry.get("bones", ())][:12]}
    log = (out / "unreal.log").read_text(encoding="utf-8", errors="replace") if (out / "unreal.log").exists() else ""
    return {"exit": code, "crash_reporters_stopped": reporters, "engine": result.get("engine"), "rigs": graded,
            "error": result.get("error"),
            "characterizer_log": [line.strip() for line in log.splitlines()
                                  if "template" in line.lower() or "characteriz" in line.lower()][:30]}


def tally(summary):
    """Success: every certain prediction agrees with its engine. Uncertain ones (``agree`` None) are counted apart."""
    results = [r["agree"] for engine in ("godot", "unity", "unreal")
               for r in summary.get(engine, {}).get("rigs", {}).values()]
    certain = [r for r in results if r is not None]
    summary["counts"] = {"agree": sum(certain), "disagree": len(certain) - sum(certain),
                         "uncertain": len(results) - len(certain)}
    summary["success"] = bool(certain) and all(certain)


def regrade(out):
    """Grade a finished run again from its saved engine output (``--regrade``), without running an engine."""
    angelo = json.loads((out / "angelo.json").read_text(encoding="utf-8"))
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    if (out / "godot-read.json").exists():
        mapped = json.loads((out / "godot-map.json").read_text(encoding="utf-8"))
        read = json.loads((out / "godot-read.json").read_text(encoding="utf-8"))
        summary["godot"] = grade_godot(mapped["profile"], read, angelo)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True, help="the Angelo that builds the rigs (background only)")
    parser.add_argument("--out", required=True)
    parser.add_argument("--godot")
    parser.add_argument("--unity")
    parser.add_argument("--unreal")
    parser.add_argument("--regrade", action="store_true", help="grade --out's saved engine output again")
    parser.add_argument("--extra", nargs="*", default=[],
                        help="held-out rig files Unreal also imports (Angelo's verdicts first; make_rigs.py)")
    parser.add_argument("--work", default=str(Path.home() / "aue.noindex"),
                        help="a short folder for Unreal's project, HOME and cache (deleted afterwards)")
    args = parser.parse_args(argv)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    if args.regrade:
        summary = regrade(out)
        tally(summary)
        (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(("PASS" if summary["success"] else "FAIL"), "summary:", out / "summary.json")
        return 0 if summary["success"] else 1
    angelo = make_rigs(args.binary, out, args.extra)
    summary = {"angelo": {rig: {key: v["verdict"] for key, v in verdicts.items()} for rig, verdicts in angelo.items()}}
    if args.godot:
        summary["godot"] = godot(args.godot, out, angelo)
    if args.unity:
        summary["unity"] = unity(args.unity, out, angelo)
    if args.unreal:
        work = Path(args.work, time.strftime("%H%M%S")).resolve()   # Unreal refuses a cache path over 119 characters
        work.mkdir(parents=True)
        try:
            summary["unreal"] = unreal(args.unreal, out, angelo, work)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    summary["seconds"] = round(time.monotonic() - started)
    tally(summary)
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(("PASS" if summary["success"] else "FAIL"), "summary:", out / "summary.json")
    return 0 if summary["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
