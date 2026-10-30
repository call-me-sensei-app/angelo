# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""End-to-end check of the Godot live link with a real Angelo and a real Godot editor, both without windows
(doc/angelo/godot_link.rst, Validation). A host-Python driver for run_native_suites.py, or by hand:

    python3 tests/python/angelo_agent/test_blender_godot_link_e2e.py --binary /abs/Angelo.app/Contents/MacOS/Angelo \\
        --out DIR [--godot GODOT] [--only RUN ...]

Runs (``RUNS``): a .glb and a .blend asset; a .glb in a project that raises every GDScript warning to an error;
and a restart, where the first Angelo is killed (or quits) mid-link and the plugin must restore the scene, say so
once and link with a second Angelo. Each builds a throwaway Godot project (the shipped plugin from
``godot_addon/angelo_link``, the test driver from ``godot_link_e2e/driver_addon``, a scene with two instances of
crate.glb and one of crate.blend), imports it with Godot's Blender importer pointed at the Angelo under test, then
runs ``godot_link_e2e/angelo_side.py`` in a background Angelo and the Godot editor ``--headless`` with the driver,
and checks both reports and that Godot logged no script error. HOME, Angelo's state and the link record are private
to the run, so neither the person's Godot settings nor their Angelo are touched. Standard library only.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent / "godot_link_e2e"
ROOT = HERE.parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent/godot_addon/angelo_link"
DRIVER = HERE / "driver_addon/angelo_link_e2e"
SECONDS = 180
GODOT = os.environ.get("ANGELO_QA_GODOT") or "/Applications/Godot.app/Contents/MacOS/Godot"

MAKE_ASSETS = """
import bpy, sys
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_cube_add(size=2)
bpy.context.object.name = "Crate"
material = bpy.data.materials.new("Wood")
material.diffuse_color = (0.8, 0.4, 0.1, 1.0)
bpy.context.object.data.materials.append(material)
folder = sys.argv[-1]
bpy.ops.wm.save_as_mainfile(filepath=folder + "/crate.blend")
bpy.ops.export_scene.gltf(filepath=folder + "/crate.glb", export_format="GLB")
"""

PROJECT = """config_version=5

[application]

config/name="Angelo Link E2E"

[editor_plugins]

enabled=PackedStringArray("res://addons/angelo_link/plugin.cfg", "res://addons/angelo_link_e2e/plugin.cfg")
"""

SCENE = """[gd_scene load_steps=3 format=3]

[ext_resource type="PackedScene" path="res://crate.glb" id="1_glb"]
[ext_resource type="PackedScene" path="res://crate.blend" id="2_blend"]

[node name="Main" type="Node3D"]

[node name="CrateGlb" parent="." instance=ExtResource("1_glb")]

[node name="CrateGlb2" parent="." instance=ExtResource("1_glb")]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 4, 0, 0)

[node name="CrateBlend" parent="." instance=ExtResource("2_blend")]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, -4, 0, 0)
"""


def _environment(home, record, extra=()):
    env = {key: os.environ[key] for key in ("PATH", "LANG", "TMPDIR", "USER", "LOGNAME", "SHELL") if key in os.environ}
    env.update(HOME=str(home), CFFIXED_USER_HOME=str(home), ANGELO_LINK_RECORD=str(record),
               ANGELO_STATE_DIR=str(home / "angelo-state"), ANGELO_DISABLE_KEYCHAIN="1",
               ANGELO_DISABLE_INTRO="1", ANGELO_AGENT_RUNTIME="off")
    env.update(extra)
    return env


def build_project(folder, angelo, godot, home, record, strict=False):
    project = folder / "project"
    (project / "addons").mkdir(parents=True)
    shutil.copytree(ADDON, project / "addons/angelo_link")
    shutil.copytree(DRIVER, project / "addons/angelo_link_e2e")
    text = PROJECT.replace("[editor_plugins]", strict_settings() + "[editor_plugins]") if strict else PROJECT
    (project / "project.godot").write_text(text, encoding="utf-8")
    env = _environment(home, record)
    subprocess.run([angelo, "--background", "--factory-startup", "--python-expr", MAKE_ASSETS, "--", str(project)],
                   check=True, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    (project / "main.tscn").write_text(SCENE, encoding="utf-8")
    settings = home / "Library/Application Support/Godot"
    settings.mkdir(parents=True, exist_ok=True)
    version = subprocess.run([godot, "--version"], capture_output=True, text=True, timeout=60).stdout.strip()
    series = ".".join(version.split(".")[:2])
    (settings / ("editor_settings-%s.tres" % series)).write_text(
        '[gd_resource type="EditorSettings" format=3]\n\n[resource]\n'
        'filesystem/import/blender/blender_path = "%s"\n' % angelo, encoding="utf-8")
    subprocess.run([godot, "--headless", "--path", str(project), "--import"], env=env, timeout=300,
                   stdout=(folder / "godot-import.log").open("w"), stderr=subprocess.STDOUT)
    return project, version


STRICT_WARNINGS = """unassigned_variable unassigned_variable_op_assign unused_variable unused_local_constant
unused_private_class_variable unused_parameter unused_signal shadowed_variable shadowed_variable_base_class
shadowed_global_identifier unreachable_code unreachable_pattern standalone_expression standalone_ternary
incompatible_ternary untyped_declaration inferred_declaration unsafe_property_access unsafe_method_access unsafe_cast
unsafe_call_argument unsafe_void_return return_value_discarded static_called_on_instance missing_tool
redundant_static_unload redundant_await missing_await assert_always_true assert_always_false integer_division
narrowing_conversion int_as_enum_without_cast int_as_enum_without_match enum_variable_without_default empty_file
deprecated_keyword confusable_identifier confusable_local_declaration confusable_local_usage
confusable_capture_reassignment confusable_temporary_modification inference_on_variant native_method_override
get_node_default_without_onready onready_with_export property_used_as_function constant_used_as_function
function_used_as_property""".split()
# Each run: (name, asset kind, strict project, how the first Angelo leaves: "" none, "kill" or "quit").
RUNS = (("glb", "glb", False, ""), ("blend", "blend", False, ""), ("glb-strict", "glb", True, ""),
        ("restart-kill", "glb", False, "kill"), ("restart-quit", "glb", True, "quit"))


def strict_settings():
    """A project.godot block that raises every GDScript warning to an error, addons included: projects do this, and
    the plugin must still load."""
    lines = ["[debug]", "", "gdscript/warnings/exclude_addons=false",
             'gdscript/warnings/directory_rules={"res://addons": 1}']
    return "\n".join(lines + ["gdscript/warnings/%s=2" % name for name in STRICT_WARNINGS]) + "\n\n"


def _angelo(angelo, script, env, log):
    return subprocess.Popen([angelo, "--background", "--factory-startup", "--python-exit-code", "1",
                             "--python", str(script)], env=env, stdout=log.open("w"), stderr=subprocess.STDOUT)


def run_one(name, kind, angelo, godot, out, strict=False, leave=""):
    folder = out / name
    folder.mkdir(parents=True)
    home = folder / "home"
    home.mkdir()
    record = folder / "link-record.json"
    project, version = build_project(folder, angelo, godot, home, record, strict)
    extra = {"ANGELO_QA_DIR": str(folder), "ANGELO_E2E_ASSET": "res://crate." + kind}
    if leave:
        extra["ANGELO_E2E_SCENARIO"] = "restart"
    env = _environment(home, record, extra)
    first = HERE / ("angelo_side_quit.py" if leave == "quit" else "angelo_side.py")
    side = _angelo(angelo, first, env, folder / "angelo-first.log")
    deadline = time.monotonic() + 60
    while not record.exists() and side.poll() is None and time.monotonic() < deadline:
        time.sleep(0.1)
    editor = subprocess.Popen([godot, "--headless", "--editor", "--path", str(project)], env=env,
                              stdout=(folder / "godot.log").open("w"), stderr=subprocess.STDOUT)
    marker, second, limit = folder / "angelo-quit-please", None, deadline + SECONDS
    while editor.poll() is None and time.monotonic() < limit:
        if leave and second is None and marker.exists():   # The driver asks for Angelo to go away mid-link.
            if leave == "kill":
                side.kill()
            side.wait(timeout=60)
            second = _angelo(angelo, HERE / "angelo_side.py", env, folder / "angelo-second.log")
        time.sleep(0.05)
    codes = {}
    for label, process in (("godot", editor), ("angelo", second or side)):
        try:
            codes[label] = process.wait(timeout=max(1, limit - time.monotonic()))
        except subprocess.TimeoutExpired:
            process.kill()
            codes[label] = "timeout"
    reports = {}
    for label in ("angelo", "godot"):
        path = folder / (label + ".json")
        reports[label] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"missing": True}
    checks = {"%s.%s" % (label, key): value for label, report in reports.items()
              for key, value in report.get("checks", {}).items()}
    errors = [line for line in (folder / "godot.log").read_text(errors="replace").splitlines()
              if "SCRIPT ERROR" in line or "Parse Error" in line]   # Any script error fails the run.
    checks["godot.no_script_errors"] = not errors
    ok = codes == {"godot": 0, "angelo": 0} and checks and all(checks.values())
    return {"run": name, "kind": kind, "godot": version, "ok": bool(ok), "exit": codes, "checks": checks,
            "script_errors": errors[:5], "problem": reports["godot"].get("problem"),
            "angelo_phase": reports["angelo"].get("phase"),
            "angelo_error": reports["angelo"].get("error") or reports["angelo"].get("traceback"),
            "versions_sent": reports["angelo"].get("versions_sent"),
            "last_export_ms": reports["angelo"].get("last_export_ms")}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True, help="the Angelo under test")
    parser.add_argument("--out", required=True)
    parser.add_argument("--godot", default=GODOT, help="a Godot 4.4+ editor (default: ANGELO_QA_GODOT)")
    parser.add_argument("--only", choices=[run[0] for run in RUNS], action="append",
                        help="run only these (default: every run)")
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if not os.path.isfile(args.godot):
        report = {"success": False, "error": "Godot not found; set ANGELO_QA_GODOT to a Godot 4.4+ editor binary"}
    else:
        results = [run_one(name, kind, args.binary, args.godot, out / "runs", strict, leave)
                   for name, kind, strict, leave in RUNS if not args.only or name in args.only]
        report = {"success": all(result["ok"] for result in results), "runs": results}
        for result in results:
            print(("PASS" if result["ok"] else "FAIL"), json.dumps(result))
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Report:", out / "report.json")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
