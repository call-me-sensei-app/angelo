# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where a reviewed script's time went in bpy.ops (script_operator_time), through the real executor.

Run by ``run_native_suites.py --include test_blender_script_operator_time`` (BACKGROUND). Every script runs through
``BlenderExecutor.execute("angelo_execute_python")``. It proves in the real Blender:

* a script's real ``bpy.ops`` calls are counted and named in a scene of 4,000 objects, where forty adds take over a
  second and the result states Blender's fact; the same objects made through ``bpy.data`` get no ``bpy_ops`` at all;
* a script that fails after operator calls still says where its time went;
* with the Boolean observer stacked: an applied Boolean is both measured and counted;
* a Python operator that calls ``bpy.ops`` itself counts once; an operator's ``__doc__``, ``str`` and ``repr`` inside a
  script are its own;
* a callable a script kept past its end counts nothing in a later script; ``bpy.ops`` is put back after every script;
* recorded, not asserted: milliseconds per primitive add and per ``bpy.data`` object at 4,000 objects.

No provider, account or network. Writes script-operator-time.json under ANGELO_QA_DIR.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
from pathlib import Path
import re
import sys
import time
import traceback

import _bpy
import bpy

assert bpy.app.background and "--factory-startup" in sys.argv
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import script_operator_time  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402

bpy.context.preferences.filepaths.file_preview_type = "NONE"
report = {"checks": {}, "observed": {}, "binary": bpy.app.binary_path, "success": False}
W = bpy.context.window
REAL_FACTORY = _bpy.ops.create_function
restored = []
CUBES = ("for i in range(%d):\n"
         "    bpy.ops.mesh.primitive_cube_add(size=1, location=(i, 0, 5))\n")
DATA = ("me = bpy.data.meshes.new('C')\n"
        "me.from_pydata([(x, y, z) for x in (0, 1) for y in (0, 1) for z in (0, 1)], [],\n"
        "               [(0,1,3,2),(4,6,7,5),(0,4,5,1),(2,3,7,6),(0,2,6,4),(1,5,7,3)])\n"
        "for i in range(%d):\n"
        "    o = bpy.data.objects.new('C', me); o.location = (i, 0, 5)\n"
        "    bpy.context.scene.collection.objects.link(o)\n")


def check(name, passed, **details):
    report["checks"][name] = bool(passed)
    if not passed:
        report.setdefault("failures", {})[name] = details


def fresh(count):
    """An empty file, then ``count`` objects through bpy.data (not a script's own time)."""
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)
    mesh = bpy.data.meshes.new("Base")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    for index in range(count):
        obj = bpy.data.objects.new("Base %d" % index, mesh)
        obj.location = (index % 64, index // 64, 0)
        bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.update()


def script(code):
    begun = time.perf_counter()
    result = BlenderExecutor(W).execute("angelo_execute_python", {"description": "QA operator time", "code": code})
    restored.append(bpy.ops._op_create_function is REAL_FACTORY)
    return result, (time.perf_counter() - begun) * 1000


def counted():
    fresh(4000)
    result, _ms = script("import bpy\n" + CUBES % 40 + "bpy.ops.object.shade_smooth()\nresult = {'ok': True}\n")
    facts = result.get("bpy_ops") or {}
    check("real_operator_calls_are_counted_and_named",
          result.get("success") and facts.get("calls") == 41
          and facts.get("costliest", [[None]])[0][:2] == ["mesh.primitive_cube_add", 40]
          and ["object.shade_smooth", 1] in [row[:2] for row in facts.get("costliest", [])], result=facts)
    check("forty_adds_at_4000_objects_take_over_a_second_and_state_the_cost_fact",
          facts.get("seconds", 0) >= script_operator_time.NOTE_SECONDS
          and facts.get("cost") == script_operator_time.COST, result=facts)
    report["observed"]["ms_per_primitive_add_at_4000"] = round(
        sum(row[2] for row in facts.get("costliest", []) if row[0] == "mesh.primitive_cube_add") / 40 * 1000, 2)
    report["observed"]["seconds_40_adds_at_4000"] = facts.get("seconds")
    fresh(4000)
    data, ms = script("import bpy\n" + DATA % 40 + "result = {'ok': True}\n")
    check("objects_made_through_bpy_data_state_no_operators", data.get("success") and "bpy_ops" not in data,
          result={key: data.get(key) for key in ("success", "bpy_ops", "error")})
    report["observed"]["ms_40_data_objects_at_4000_whole_script"] = round(ms, 1)


def failed():
    fresh(10)
    result, _ms = script("import bpy\n" + CUBES % 3 + "raise ValueError('after three adds')\n")
    check("a_failed_script_still_says_where_its_time_went",
          result.get("success") is not True and (result.get("bpy_ops") or {}).get("calls") == 3,
          result={key: result.get(key) for key in ("success", "bpy_ops", "error")})


def stacked():
    fresh(0)
    code = ("import bpy\n"
            "bpy.ops.mesh.primitive_cube_add(size=2)\nblock = bpy.context.object\n"
            "bpy.ops.mesh.primitive_cube_add(size=1, location=(1, 0, 0))\ncutter = bpy.context.object\n"
            "mod = block.modifiers.new('Cut', 'BOOLEAN'); mod.object = cutter\n"
            "bpy.context.view_layer.objects.active = block\n"
            "bpy.ops.object.modifier_apply(modifier='Cut')\nresult = {'ok': True}\n")
    result, _ms = script(code)
    names = [row[0] for row in (result.get("bpy_ops") or {}).get("costliest", [])]
    check("an_applied_boolean_is_measured_and_counted",
          result.get("success") and len(result.get("booleans") or []) == 1
          and (result.get("bpy_ops") or {}).get("calls") == 3 and "object.modifier_apply" in names,
          result={key: result.get(key) for key in ("success", "bpy_ops", "booleans", "error")})


class QA_OT_nested_add(bpy.types.Operator):
    """A Python operator that adds two cubes through bpy.ops itself."""
    bl_idname, bl_label = "qa.nested_add", "QA nested add"

    def execute(self, context):
        bpy.ops.mesh.primitive_cube_add(size=1)
        bpy.ops.mesh.primitive_cube_add(size=1, location=(2, 0, 0))
        return {"FINISHED"}


def nested_and_documented():
    fresh(10)
    bpy.utils.register_class(QA_OT_nested_add)
    try:
        plain = bpy.ops.mesh.primitive_cube_add
        result, _ms = script("import bpy\nop = bpy.ops.mesh.primitive_cube_add\n"
                             "result = {'doc': op.__doc__, 'str': str(op), 'repr': repr(op)}\n"
                             "bpy.ops.qa.nested_add()\n")
    finally:
        bpy.utils.unregister_class(QA_OT_nested_add)
    facts = result.get("bpy_ops") or {}
    check("an_operator_calling_bpy_ops_itself_counts_once",
          result.get("success") and facts.get("calls") == 1
          and [row[:2] for row in facts.get("costliest", [])] == [["qa.nested_add", 1]], result=facts,
          error=result.get("error"))
    seen = result.get("result") or {key: result.get(key) for key in ("doc", "str", "repr")}
    unplaced = lambda text: re.sub(r" at 0x[0-9a-fA-F]+", "", str(text))   # Each lookup makes a new callable; Windows prints upper case.
    check("an_operators_documentation_and_printed_form_are_its_own",
          seen.get("doc") == plain.__doc__ and unplaced(seen.get("str")) == unplaced(str(plain))
          and unplaced(seen.get("repr")) == unplaced(repr(plain)),
          seen=seen, plain=[plain.__doc__, str(plain), repr(plain)])


def kept():
    fresh(0)
    first, _ms = script("import bpy\nbpy.app.driver_namespace['qa_kept'] = bpy.ops.mesh.primitive_cube_add\n"
                        "result = {'ok': True}\n")
    later, _ms = script("import bpy\nbpy.app.driver_namespace.pop('qa_kept')(size=1)\nresult = {'ok': True}\n")
    check("a_callable_kept_past_its_script_counts_nothing_later",
          first.get("success") and later.get("success") and "bpy_ops" not in first and "bpy_ops" not in later,
          first=first.get("bpy_ops"), later=later.get("bpy_ops"), error=later.get("error"))


try:
    counted()
    failed()
    stacked()
    nested_and_documented()
    kept()
    check("bpy_ops_is_restored_after_every_script", restored and all(restored), scripts=len(restored))
    report["success"] = all(report["checks"].values()) and bool(report["checks"])
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    (OUT / "script-operator-time.json").write_text(json.dumps(report, indent=2, default=str))
failed_checks = sorted(name for name, passed in report["checks"].items() if not passed)
print("SCRIPT_OPERATOR_TIME failed checks:", json.dumps(failed_checks))
print("SCRIPT_OPERATOR_TIME observed:", json.dumps(report["observed"]))
assert report["success"], failed_checks or report.get("error")
