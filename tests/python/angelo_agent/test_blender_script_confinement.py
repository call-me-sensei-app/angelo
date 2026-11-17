# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The agent-script confinement in the real app (doc/angelo/script_confinement.rst): Angelo enabled as it starts,
so ``install_app`` ran at registration; agent code runs through the executor's own ``run_confined``.

* Operators: a script may add a cube, but not open a URL or a path, save or install preferences, save a file into
  Angelo's state folder, save a text into Blender's startup scripts, or call a grant operator.
* What a script leaves for later is marked: a ``bpy.app.timers`` callable is registered in its confined form (and
  ``is_registered``/``unregister`` still find it), a draw handler's callable too, a handler and a
  ``driver_namespace`` function it added are confined at its end, and setting a text to run at load is refused.
* Angelo's own code keeps its access once the script has ended.
* The cost, measured in the app: an ``open()`` before any script, after scripts, and the sweep per script.

Run only in a disposable instance: ``--background --factory-startup --disable-autoexec``. No provider or paid call.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import functools
import json
import tempfile
import time
import traceback
from pathlib import Path

import addon_utils
import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-script-confinement-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("ANGELO_STATE_DIR", str(OUT / "state"))   # The runner's isolated folders when it sets them.
os.environ.setdefault("ANGELO_CONNECTIONS_DIR", str(OUT / "connections"))
os.environ["ANGELO_DISABLE_KEYCHAIN"] = "1"
REPORT = {"success": False, "checks": {}, "numbers": {}}
MARKER = OUT / "program-ran"


def check(name, ok, detail=None):
    REPORT["checks"][name] = bool(ok)
    if not ok and detail is not None:
        REPORT.setdefault("details", {})[name] = str(detail)[:400]


def per_open(path, count=2000):
    started = time.perf_counter()
    for _ in range(count):
        with open(path) as handle:
            handle.read()
    return (time.perf_counter() - started) / count * 1e6


def main():
    from angelo_agent import script_confinement, ui
    from angelo_agent.script_confinement import AUTORUN, GRANT, OPERATOR, PRIVATE, STARTUP
    check("installed_at_registration", script_confinement.installed())
    check("native", script_confinement.native())   # The C confinement, not the Python stand-in.

    sample = OUT / "sample.txt"
    sample.write_text("x")
    REPORT["numbers"]["idle_open_us"] = per_open(sample)
    state = Path(ui.state_directory())
    key = state / "keys" / "provider.key"
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text("sk-original")
    startup = Path(bpy.utils.user_resource("SCRIPTS", path="startup"))

    def run(source, **names):
        namespace = {"__name__": "__angelo_script__", "bpy": bpy, "functools": functools, "os": os,
                     "KEY": str(key), "STATE": str(state), "STARTUP": str(startup), "MARKER": str(MARKER), **names}
        from confinement_attacks_admit import admit
        admit("suite", source)
        try:
            # What the checks inspect is read while confined (the namespace is cleared there).
            findings, kept = script_confinement.run_confined(source, namespace, None, "suite", finish=dict)
            return {"refused": None, "findings": findings, "namespace": kept}
        except script_confinement.ScriptConfined as error:
            return {"refused": error.reason}
        except Exception as error:   # noqa: BLE001 - reported as the check's detail.
            return {"refused": None, "error": repr(error)}

    before = len(bpy.data.objects)
    cube = run("bpy.ops.mesh.primitive_cube_add()")
    check("cube_allowed", cube.get("refused") is None and "error" not in cube and len(bpy.data.objects) == before + 1,
          cube)
    for name, source, reason in (
            ("url_open", "bpy.ops.wm.url_open(url='file:///tmp')", OPERATOR),
            ("path_open", "bpy.ops.wm.path_open(filepath='/tmp')", OPERATOR),
            ("save_userpref", "bpy.ops.wm.save_userpref()", OPERATOR),
            # Both reload every add-on inside the region (whose end then breaks them): refused, never run.
            ("read_factory_settings", "bpy.ops.wm.read_factory_settings(use_empty=True)", OPERATOR),
            ("app_template_switch", "bpy.ops.wm.read_homefile(app_template='Sculpting')", OPERATOR),
            ("addon_install", "bpy.ops.preferences.addon_install(filepath='/tmp/x.py')", OPERATOR),
            ("save_into_state", "bpy.ops.wm.save_as_mainfile(filepath=STATE + '/stolen.blend', copy=True)", PRIVATE),
            ("text_into_startup", ("text = bpy.data.texts.new('evil')\ntext.write('import os')\n"
                                   "text.filepath = STARTUP + '/evil.py'\n"
                                   "with bpy.context.temp_override(edit_text=text):\n    bpy.ops.text.save()"),
             STARTUP),
            ("grant_operator", "bpy.ops.angelo.external_app_connect(app='other')", GRANT),
            ("read_key", "open(KEY).read()", PRIVATE),
            ("text_runs_at_load", "t = bpy.data.texts.new('autorun')\nt.use_module = True", AUTORUN)):
        result = run(source)
        check("refused_" + name, result.get("refused") == reason, result)
    check("nothing_saved", not (state / "stolen.blend").exists() and not (startup / "evil.py").exists())

    leftovers = run(
        "timer = functools.partial(os.system, '/usr/bin/touch ' + MARKER)\n"
        "bpy.app.timers.register(timer, first_interval=3600)\n"
        "handler = functools.partial(os.remove, KEY)\n"
        "bpy.app.handlers.depsgraph_update_post.append(handler)\n"
        "bpy.app.driver_namespace['evil'] = functools.partial(os.system, '/usr/bin/touch ' + MARKER)\n"
        "text = bpy.data.texts.new('at_load')\n"
        "draw = bpy.types.SpaceView3D.draw_handler_add(functools.partial(os.system, 'x'), (), 'WINDOW', 'POST_PIXEL')")
    namespace = leftovers.get("namespace", {})
    timer = namespace.get("timer")
    check("leftovers_ran", "error" not in leftovers and leftovers.get("refused") is None, leftovers)
    # Blender kept the timer's confined form (bpy_app_timers.cc), not the callable the script passed. (A background
    # app runs no timers: that the confined form refuses is the attack suite's, with the same trampoline.)
    check("timer_kept_confined", timer is not None and not bpy.app.timers.is_registered(timer))
    import _bpy
    deferred = _bpy._angelo_guard.is_deferred
    handlers = [item for item in bpy.app.handlers.depsgraph_update_post
                if deferred(item) and item == namespace.get("handler")]
    check("handler_marked", len(handlers) == 1, list(bpy.app.handlers.depsgraph_update_post))
    for item in handlers:
        try:
            item()   # The confined form runs whatever it is called with (a handler's own arguments here would only
            # make os.remove refuse the call shape before the confinement sees it).
            check("handler_refused_later", False, "ran")
        except PermissionError:
            check("handler_refused_later", True)
        bpy.app.handlers.depsgraph_update_post.remove(item)
    evil = bpy.app.driver_namespace.pop("evil", None)
    check("driver_function_marked", evil is not None and deferred(evil))
    check("text_no_longer_runs_at_load", not bpy.data.texts["at_load"].use_module)
    findings = " ".join(leftovers.get("findings", []))
    check("findings_reported", "depsgraph_update_post" in findings, findings)
    if namespace.get("draw") is not None:
        bpy.types.SpaceView3D.draw_handler_remove(namespace["draw"], 'WINDOW')
    check("no_program_ran", not MARKER.exists())
    check("key_intact", key.read_text() == "sk-original")

    from angelo_agent import always_allow
    always_allow.set_enabled(False)   # Angelo's own code, after scripts: writes its state as before.
    check("angelo_keeps_access", not always_allow.enabled() and key.read_text() == "sk-original")
    check("not_confined_after", not script_confinement.confined())

    REPORT["numbers"]["armed_open_us"] = per_open(sample)
    out = {}
    run("out['confined_open_us'] = per_open(sample)", out=out, per_open=per_open, sample=sample)
    REPORT["numbers"]["confined_open_us"] = out.get("confined_open_us")
    from confinement_attacks_admit import admit
    plain = compile("x = 1", "<plain>", "exec")
    started = time.perf_counter()
    for _ in range(50):
        exec(plain, {})
    plain_ms = (time.perf_counter() - started) / 50 * 1000
    started = time.perf_counter()
    for _ in range(50):
        admit("suite", "x = 1")
        script_confinement.run_confined("x = 1", {}, None, "suite")
    REPORT["numbers"]["sweep_ms_per_script"] = (time.perf_counter() - started) / 50 * 1000 - plain_ms
    REPORT["numbers"]["angelo_modules"] = sum(name.startswith("angelo_agent") for name in sys.modules)


try:
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    main()
    REPORT["success"] = bool(REPORT["checks"]) and all(REPORT["checks"].values())
except Exception:   # noqa: BLE001 - the receipt says why.
    REPORT["error"] = traceback.format_exc()
(OUT / "script-confinement.json").write_text(json.dumps(REPORT, indent=2, default=str))
print(json.dumps(REPORT, default=str)[:4000])
if REPORT["success"]:
    print("ANGELO_SCRIPT_CONFINEMENT_OK")
sys.exit(0 if REPORT["success"] else 1)
