# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Game physics end to end: Angelo's OMI export imported by real Godot editors, headless (doc/angelo/game_physics.rst).

    python3 tests/python/angelo_agent/test_blender_godot_physics_e2e.py \\
        --binary /abs/Angelo.app/Contents/MacOS/Angelo \\
        --out DIR [--godot GODOT ...] [--assets FOLDER_OF_GLB] [--timeout SECONDS]

``godot_physics_e2e/angelo_side.py`` marks every model in ``--assets`` (``ANGELO_QA_PHYSICS_ASSETS``: held-out
models this feature was not developed on, such as a free CC0 kit) with each body and shape by one rule, and exports
them; two of them also go in as .blend files, which Godot imports through the Angelo under test as its Blender (a
background Angelo). Each Godot editor (``--godot``, repeatable; ``ANGELO_QA_GODOT``, several joined by the path
separator) imports the project headless and ``godot_physics_e2e/dump_physics.gd`` reports what it built. The checks
compare that with what each variant asked for, independently of Angelo's own arithmetic:

* each variant's root is the body Godot makes for its type (RigidBody3D with the mass, AnimatableBody3D, Area3D,
  StaticBody3D), every mesh survived, every shape is a CollisionShape3D directly below the body and is the chosen
  type, and Godot made a separate body for exactly the shapes Angelo's ``shape_indirect`` finding flagged (a mesh
  that moves inside its body, such as one on an animated bone);
* each fitted primitive matches the bounds Godot itself measures of the mesh it was fitted to at rest, in the shape's
  frame (a box is the bounds, a sphere's radius half the longest side, a capsule or cylinder runs along the longest
  side; a skinned mesh is measured where its skin draws it, bone rest times bind pose, not at its own node);
* a convex or mesh shape's own points span its mesh where Godot draws it (a skinned mesh through its skin);
* a Mesh shape without a body is a StaticBody3D below its mesh; Godot's import log has no glTF physics error.

HOME and Godot's settings are private to the run. Standard library only.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent / "godot_physics_e2e"
SHAPE_CLASSES = {"BOX": "BoxShape3D", "SPHERE": "SphereShape3D", "CAPSULE": "CapsuleShape3D",
                 "CYLINDER": "CylinderShape3D", "CONVEX": "ConvexPolygonShape3D", "TRIMESH": "ConcavePolygonShape3D"}
PROJECT = '[application]\n\nconfig/name="Angelo physics E2E"\n'
TIMEOUT = 600


def near(a, b, relative=2e-3, absolute=2e-3):
    return abs(a - b) <= max(absolute, relative * max(abs(a), abs(b)))


def environment(home):
    env = {key: os.environ[key] for key in ("PATH", "LANG", "TMPDIR", "USER", "LOGNAME", "SHELL") if key in os.environ}
    env.update(HOME=str(home), CFFIXED_USER_HOME=str(home), ANGELO_STATE_DIR=str(home / "angelo-state"),
               ANGELO_DISABLE_KEYCHAIN="1", ANGELO_DISABLE_INTRO="1", ANGELO_AGENT_RUNTIME="off")
    return env


def godot_settings(home, godot, angelo, env):
    version = subprocess.run([godot, "--headless", "--version"], capture_output=True, text=True, env=env,
                             timeout=120).stdout.strip().splitlines()[-1]
    series = ".".join(version.split(".")[:2])
    for folder in (home / "Library/Application Support/Godot", home / ".config/godot"):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / ("editor_settings-%s.tres" % series)).write_text(
            '[gd_resource type="EditorSettings" format=3]\n\n[resource]\n'
            'filesystem/import/blender/blender_path = "%s"\n' % angelo, encoding="utf-8")
    return version


def shape_problems(shape, row):
    """Why one CollisionShape3D row doesn't match the shape asked for and the mesh Godot measured, or []."""
    problems = []
    if row["class"] != SHAPE_CLASSES[shape]:
        return ["%s is %s, not %s" % (row["name"], row["class"], SHAPE_CLASSES[shape])]
    if shape in ("CONVEX", "TRIMESH"):   # Its own points must lie where Godot draws the mesh.
        if row.get("points", row.get("faces", 0)) <= 0:
            return ["%s is empty" % row["name"]]
        if "mesh_size" not in row:
            return ["%s: %s" % (row["name"], row.get("mesh_unmeasured", "no mesh to compare with"))]
        if not (all(near(a, b) for a, b in zip(row["shape_size"], row["mesh_size"]))
                and all(near(a, b) for a, b in zip(row["shape_center"], row["mesh_center"]))):
            return ["%s spans %s at %s, its mesh %s at %s" % (row["name"], row["shape_size"], row["shape_center"],
                                                                row["mesh_size"], row["mesh_center"])]
        return []
    if not all(near(value, 1.0) for value in row["scale"]):
        problems.append("%s is scaled %s" % (row["name"], row["scale"]))
    if "mesh_size" not in row:
        return problems + ["%s: %s" % (row["name"], row.get("mesh_unmeasured", "no mesh to compare with"))]
    size, center = row["mesh_size"], row["mesh_center"]
    if not all(near(value, 0.0) for value in center):
        problems.append("%s is off its mesh's centre by %s" % (row["name"], center))
    if shape == "BOX":
        if not all(near(a, b) for a, b in zip(row["size"], size)):
            problems.append("%s box %s, mesh bounds %s" % (row["name"], row["size"], size))
    elif shape == "SPHERE":
        if not near(row["radius"], max(size) / 2):
            problems.append("%s radius %s, mesh bounds %s" % (row["name"], row["radius"], size))
    else:
        radial = max(size[0], size[2]) / 2
        height = max(size[1], 2 * radial) if shape == "CAPSULE" else size[1]
        # An automatic axis is the longest side, which the shape's +Y follows.
        along = size[1] + 2e-3 >= max(size[0], size[2])
        if not (near(row["radius"], radial) and near(row["height"], height) and along):
            problems.append("%s %s radius %s height %s, mesh bounds %s" % (
                row["name"], shape.lower(), row["radius"], row["height"], size))
    return problems


def check_scene(name, dumped, expected):
    """Problems of one imported scene against angelo_side's expectations."""
    if dumped is None:
        return ["%s didn't load" % name]
    problems = []
    statics = [item for variant in dumped.values() for item in variant.get("statics", [])]
    for key, want in expected.items():
        if "static_meshes" in want:
            for mesh in want["static_meshes"]:
                rows = [item for item in statics if item["parent"] == mesh]
                if not rows or not all(row["class"] == SHAPE_CLASSES[want["shape"]] for item in rows
                                       for row in item["shapes"]) or not rows[0]["shapes"]:
                    problems.append("%s: %s has no static %s collider" % (name, mesh, want["shape"].lower()))
                    continue
                problems += ["%s: %s" % (name, problem) for item in rows for row in item["shapes"]
                             for problem in shape_problems(want["shape"], row)]
            continue
        got = dumped.get(want["root"])
        if got is None:
            problems.append("%s: no %s node" % (name, want["root"]))
            continue
        label = "%s/%s" % (name, want["root"])
        if got["class"] != want["class"]:
            problems.append("%s is %s, not %s" % (label, got["class"], want["class"]))
        if want.get("mass") is not None and not near(got.get("mass", 0), want["mass"]):
            problems.append("%s mass %s" % (label, got.get("mass")))
        if got["meshes"] != want["meshes"]:
            problems.append("%s has %d meshes, the model %d" % (label, got["meshes"], want["meshes"]))
        if len(got["shapes"]) != want["shapes"]:
            problems.append("%s has %d shapes directly below it, not %d" % (label, len(got["shapes"]), want["shapes"]))
        separate = sorted({item["parent"] for item in got["statics"]} | set(got.get("areas", [])))
        if separate != want.get("flagged", []):   # Exactly the shapes Angelo's findings said Godot separates.
            problems.append("%s: Godot separated %s, Angelo flagged %s" % (label, separate, want.get("flagged", [])))
        for row in got["shapes"]:
            problems += ["%s: %s" % (label, problem) for problem in shape_problems(want["shape"], row)]
    return problems


def run_godot(godot, angelo, out, made, timeout=TIMEOUT):
    home = out / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = environment(home)
    version = godot_settings(home, godot, angelo, env)
    project = out / "project"
    shutil.copytree(made, project)
    (project / "project.godot").write_text("config_version=5\n\n" + PROJECT, encoding="utf-8")
    with (out / "godot-import.log").open("w") as log:
        subprocess.run([godot, "--headless", "--path", str(project), "--import"], env=env, timeout=timeout,
                       stdout=log, stderr=subprocess.STDOUT)
    expected = json.loads((project / "expected.json").read_text(encoding="utf-8"))
    paths = ["res://" + name for name in expected]
    dump = subprocess.run([godot, "--headless", "--path", str(project), "--script", str(HERE / "dump_physics.gd"),
                           "--"] + paths, env=env, capture_output=True, text=True, timeout=timeout)
    (out / "godot-dump.log").write_text(dump.stdout + dump.stderr, encoding="utf-8")
    line = next((item for item in dump.stdout.splitlines() if item.startswith("ANGELO_PHYSICS_DUMP ")), None)
    dumped = json.loads(line.split(" ", 1)[1]) if line else {}
    problems = []
    for name, rows in expected.items():
        problems += check_scene(name, dumped.get("res://" + name), rows)
    log = (out / "godot-import.log").read_text(errors="replace")
    errors = [item for item in log.splitlines() if "ERROR" in item and ("Physics" in item or "physics" in item)]
    problems += ["Godot: " + item for item in errors[:10]]
    variants = sum(len(rows) for rows in expected.values())
    return {"godot": version, "ok": not problems and bool(dumped), "scenes": len(expected), "variants": variants,
            "problems": problems[:60]}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True, help="the Angelo under test")
    parser.add_argument("--out", required=True)
    parser.add_argument("--godot", action="append", help="a Godot 4.4+ editor; repeat for several versions "
                        "(default: ANGELO_QA_GODOT, several joined by %r)" % os.pathsep)
    parser.add_argument("--assets", default=os.environ.get("ANGELO_QA_PHYSICS_ASSETS"),
                        help="a folder of held-out .glb models (default: ANGELO_QA_PHYSICS_ASSETS)")
    parser.add_argument("--blends", type=int, default=2, help="models also imported as .blend through Angelo")
    parser.add_argument("--timeout", type=int, default=TIMEOUT, help="seconds each Angelo or Godot step may take "
                        "(default %(default)s; a large asset set or a loaded machine needs more)")
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    godots = args.godot or [item for item in os.environ.get("ANGELO_QA_GODOT", "").split(os.pathsep) if item]
    missing = [item for item in godots if not os.path.isfile(item)]
    if not godots or missing or not args.assets or not list(Path(args.assets).glob("*.glb")):
        report = {"success": False, "error": "Set ANGELO_QA_GODOT (Godot 4.4+ editors) and ANGELO_QA_PHYSICS_ASSETS "
                                             "(a folder of .glb models)", "missing": missing}
    else:
        made = out / "made"
        made.mkdir(exist_ok=True)
        home = out / "angelo-home"
        home.mkdir(exist_ok=True)
        with (out / "angelo.log").open("w") as log:
            code = subprocess.run([args.binary, "--background", "--factory-startup", "--python-exit-code", "1",
                                   "--python", str(HERE / "angelo_side.py"), "--", str(Path(args.assets).resolve()),
                                   str(made), str(args.blends)], env=environment(home), stdout=log,
                                  stderr=subprocess.STDOUT, timeout=args.timeout).returncode
        if code != 0 or not (made / "expected.json").exists():
            report = {"success": False, "error": "Angelo's side failed (angelo.log)", "exit": code}
        else:
            runs = [run_godot(godot, args.binary, out / ("godot-%d" % index), made, args.timeout)
                    for index, godot in enumerate(godots)]
            report = {"success": all(run["ok"] for run in runs), "runs": runs,
                      "assets": sorted(path.name for path in Path(args.assets).glob("*.glb"))}
            for run in runs:
                print("PASS" if run["ok"] else "FAIL", json.dumps(run))
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Report:", out / "report.json")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
