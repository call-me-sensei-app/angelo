# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where the confinement holds whatever route a script takes, for test_blender_confinement_escapes (Devin break-it
Phase C, 2026-11-15). Each check failed before its fix; each judges the place the effect lands, never one route.

* MUST-4: the environment, the working folder and the file-creation mask are refused to confined code, inside a
  region or not; the other process-wide settings a generator the script left changes after its run are put back
  when its step ends (it runs as a region) or refused, and so is what it rebinds in installed modules.
* SHOULD-1: a driver's Python in an embedded ID (a material's node tree) is disarmed like an ID's own.
"""

import json
import os
import subprocess
import sys

import bpy

import child_blender   # A child Angelo runs the suite's code from its own file.

CHILD_ENV = ("ANGELO_PROBE_SINK", "LD_PRELOAD", "TZ", "HOME")


def _child_environment():
    """The C environment a program started now inherits (not `os.environ`, the mapping)."""
    code = "import json, os; print(json.dumps({k: os.environ.get(k) for k in %r}))" % (CHILD_ENV,)
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)
    return json.loads(done.stdout.strip().splitlines()[-1])


def _umask():
    value = os.umask(0o022)
    os.umask(value)
    return value


def _settled():
    """Run some bytecode so pending calls (where code outside a region is settled) get their turn."""
    total = 0
    for i in range(5000):
        total += i
    return total


def _resume(run, body, **names):
    """A generator the script leaves, resumed after its run (outside any region): its first step's outcome."""
    holder = {}
    outcome = run("import os, sys, gc, locale, atexit, codecs, builtins, unittest.mock\n"
                  "def later():\n" + "".join("    %s\n" % line for line in body.splitlines()) +
                  "    yield 'done'\n"
                  "HELD['generator'] = later()\n", HELD=holder, **names)
    if "generator" not in holder:
        return "not made: %r" % (outcome,)
    try:
        step = str(next(holder["generator"]))
    except Exception as error:   # noqa: BLE001 - a refusal is the pass.
        step = type(error).__name__
    _settled()
    return step


def _state():
    return {"env": _child_environment(), "environ": {k: os.environ.get(k) for k in CHILD_ENV}, "cwd": os.getcwd(),
            "umask": _umask()}


def process_state(run, check, plain):
    before = _state()
    sources = {
        "environ": "import os\nos.environ['LD_PRELOAD'] = PLAIN + '/written-by-the-script.so'\n",
        "putenv": "import os\nos.putenv('ANGELO_PROBE_SINK', 'planted')\n",
        "unsetenv": "import os\nos.unsetenv('HOME')\n",
        "time_zone": "import os\nos.environ['TZ'] = 'Asia/Kathmandu'\n",
        "chdir": "import os\nos.chdir(PLAIN)\n",
        "umask": "import os\nos.umask(0)\n",
    }
    refused = {name: run(source, PLAIN=str(plain))["refused"] for name, source in sources.items()}
    after = _state()
    check("process_state_refused_in_region", all(reason is not None for reason in refused.values()) and
          before == after, (refused, before, after))
    steps = {name: _resume(run, body, PLAIN=str(plain)) for name, body in (
        ("environ", "os.environ['LD_PRELOAD'] = PLAIN + '/written-by-the-script.so'"),
        ("putenv", "os.putenv('ANGELO_PROBE_SINK', 'planted')"),
        ("time_zone", "os.environ['TZ'] = 'Asia/Kathmandu'"),
        ("chdir", "os.chdir(PLAIN)"),
        ("umask", "os.umask(0)"))}
    after = _state()
    check("process_state_refused_from_generator", all(step == "ScriptConfined" for step in steps.values()) and
          before == after, (steps, before, after))


def settings_from_generator(run, check):
    import gc
    import locale
    before = {"gc": (gc.isenabled(), gc.get_threshold()), "recursion": sys.getrecursionlimit(),
              "interval": sys.getswitchinterval(), "locale": locale.setlocale(locale.LC_ALL, None)}
    steps = {name: _resume(run, body) for name, body in (
        ("gc", "gc.disable()"),
        ("threshold", "gc.set_threshold(0)"),
        ("recursion", "sys.setrecursionlimit(54321)"),
        ("interval", "sys.setswitchinterval(0.25)"),
        ("locale", "locale.setlocale(locale.LC_NUMERIC, 'C')"),
        ("atexit", "atexit.register(print, 'left by the script')"),
        ("codecs", "codecs.register(lambda name: None)"))}
    after = {"gc": (gc.isenabled(), gc.get_threshold()), "recursion": sys.getrecursionlimit(),
             "interval": sys.getswitchinterval(), "locale": locale.setlocale(locale.LC_ALL, None)}
    # A step of a generator the script left runs as a region (escapes_review.py): its changes are put back when the
    # step ends, as a region's are, or refused.
    check("settings_from_generator_put_back", all(step in ("done", "ScriptConfined") for step in steps.values()) and
          before == after, (steps, before, after))
    # In a region the same changes still work, and are put back at its end (no false refusal).
    outcome = run("import sys, gc\nsys.setrecursionlimit(54321)\ngc.disable()\nHELD['r'] = sys.getrecursionlimit()\n",
                  HELD=(held := {}))
    check("settings_in_region_work", outcome["refused"] is None and held.get("r") == 54321 and
          sys.getrecursionlimit() == before["recursion"] and gc.isenabled() == before["gc"][0], (outcome, held))


def namespaces_from_generator(run, check):
    import builtins
    import os.path
    join, exists = os.path.join, os.path.exists
    step = _resume(run, "builtins.angelo_probe_builtin = lambda: 'planted'\n"
                        "os.path.join = lambda *parts: 'planted'\n"
                        "unittest.mock.patch.object(os.path, 'exists', lambda path: True).start()")
    planted = {"builtin": hasattr(builtins, "angelo_probe_builtin"), "join": os.path.join is not join,
               "exists": os.path.exists is not exists}
    for name, value in (("join", join), ("exists", exists)):
        setattr(os.path, name, value)
    if hasattr(builtins, "angelo_probe_builtin"):
        del builtins.angelo_probe_builtin
    check("namespaces_put_back_from_generator", step == "done" and not any(planted.values()), (step, planted))
    # Its own state stays: a generator keeping a count in its own module works on.
    holder = {}
    run("COUNT = [0]\ndef later():\n    global TOTAL\n    for i in range(3):\n        COUNT[0] += 1\n"
        "        TOTAL = COUNT[0]\n        yield TOTAL\nHELD['generator'] = later()\n", HELD=holder)
    steps = list(holder.get("generator", ()))
    _settled()
    check("generator_own_state_kept", steps == [1, 2, 3], steps)


def _child_value(path, expression):
    """A fresh app with auto-run on opens `path`, evaluates frame 3 and prints `expression`'s value."""
    code = ("import addon_utils, bpy, json\n"
            "addon_utils.enable('angelo_agent', default_set=False, persistent=False)\n"
            "bpy.ops.wm.open_mainfile(filepath=%r, load_ui=False, use_scripts=True)\n"
            "ob = bpy.data.objects['probe']\nscene = ob.users_scene[0]\nscene.frame_set(3)\n"
            "depsgraph = scene.view_layers[0].depsgraph\n"
            "print('CHILD ' + json.dumps({'value': %s}))\n") % (str(path), expression)
    done = child_blender.run(code, capture_output=True, text=True, timeout=600)
    for line in done.stdout.splitlines():
        if line.startswith("CHILD "):
            return json.loads(line[6:])["value"]
    return "no result: " + done.stdout[-400:] + done.stderr[-400:]


NODE_VALUE = ("bpy.data.materials['probe_mat'].evaluated_get(depsgraph).node_tree"
              ".nodes['probe_value'].outputs[0].default_value")


def _embedded_driver_file(path, expression):
    """A file the person made: a mesh whose material's own node tree (an embedded ID) has a scripted driver."""
    scene = bpy.data.scenes.new("probe_scene")
    mesh = bpy.data.meshes.new("probe_mesh")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    material = bpy.data.materials.new("probe_mat")
    tree = material.node_tree
    node = tree.nodes.new("ShaderNodeValue")
    node.name = "probe_value"
    node.outputs[0].default_value = 0.0
    output = tree.nodes.get("Material Output") or tree.nodes.new("ShaderNodeOutputMaterial")
    tree.links.new(node.outputs[0], output.inputs["Displacement"])
    driver = node.outputs[0].driver_add("default_value").driver
    driver.type = 'SCRIPTED'
    driver.expression = expression
    mesh.materials.append(material)
    ob = bpy.data.objects.new("probe", mesh)
    scene.collection.objects.link(ob)
    bpy.data.libraries.write(str(path), {scene, ob}, fake_user=True)
    bpy.data.objects.remove(ob)
    bpy.data.meshes.remove(mesh)
    bpy.data.materials.remove(material)
    bpy.data.scenes.remove(scene)


def embedded_driver(run, check, plain):
    for ob in [ob for ob in bpy.data.objects if ob.name.startswith("probe")]:
        bpy.data.objects.remove(ob)   # Earlier checks' (the file's object must be named "probe").
    source = plain / "embedded-driver.blend"
    _embedded_driver_file(source, "(lambda: 7)()")
    own = _child_value(source, NODE_VALUE)
    saved = plain / "embedded-driver-appended.blend"
    outcome = run("with bpy.data.libraries.load(SOURCE, link=False) as (src, dst):\n"
                  "    dst.objects = ['probe']\n"
                  "bpy.context.scene.collection.objects.link(dst.objects[0])\n", SOURCE=str(source))
    bpy.ops.wm.save_as_mainfile(filepath=str(saved), copy=True)   # The person, unconfined, later.
    appended = _child_value(saved, NODE_VALUE)
    check("person_embedded_driver_works", own == 7, own)
    check("appended_embedded_driver_inert", outcome["refused"] is None and appended == 0, (outcome, appended))
    for ob in [ob for ob in bpy.data.objects if ob.name.startswith("probe")]:
        bpy.data.objects.remove(ob)
    for material in [m for m in bpy.data.materials if m.name.startswith("probe_mat")]:
        bpy.data.materials.remove(material)


def run_all(run, check, plain):
    process_state(run, check, plain)
    settings_from_generator(run, check)
    namespaces_from_generator(run, check)
    embedded_driver(run, check, plain)
