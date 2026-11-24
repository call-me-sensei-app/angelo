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
* A script may not register an operator that runs while a job locks the interface (``LOCK_BYPASS``); the app may.
* Once registered, an operator type's options and strings never change, through its own instance or the person's
  operator history, confined or not: an option write raises, a string write is upstream's silent no-op.
* No network through a native reader or writer: a localhost listener sees no connection when a script, or Angelo's
  own code after it, gives a URL to a sound, movie, image, font, strip, USD or Alembic loader or a sound writer.
* Hot paths: the count they read first (``confine_open``) is zero with no script and no agent code alive, set while a
  script runs and while a function it left lives, and a function it left in an immutable holder is still confined
  when Angelo's code calls it after the region (the stack test; BREAKIT script-isolation-review-fable-1 A-MUST-1),
  as is one made from code renamed to the agent's name, or nested deeper than any fixed bound (BREAKIT
  script-isolation-review-fable-2 A2-MUST-1, break-it-confine-count-14a2d702b19 MUST-1).
* The cost, measured in the app: an ``open()`` before any script, after scripts, and the sweep per script.

Run only in a disposable instance: ``--background --factory-startup --disable-autoexec``. No provider or paid call.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import functools
import gc
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
REPORT = {"success": False, "checks": {}, "numbers": {}, "skipped": {}}
MARKER = OUT / "program-ran"


def check(name, ok, detail=None):
    REPORT["checks"][name] = bool(ok)
    if not ok and detail is not None:
        REPORT.setdefault("details", {})[name] = str(detail)[:400]


def skip(name, reason):
    """A check that could not be judged here, with why: reported apart from the checks, never as a pass."""
    REPORT["skipped"][name] = str(reason)[:400]


def count_holders(limit=12):
    """What keeps the hot-path count set, for a failing "free" check: the three parts (states, codes, always) and,
    for live agent code, what holds it (a function, a frame, a container: its type and name). Code objects are not
    tracked by the collector, so they are found through what holds them."""
    import _bpy
    guard = _bpy._angelo_guard
    counts = guard.confine_counts() if hasattr(guard, "confine_counts") else {"open": guard.confine_open()}
    gc.collect()
    found = []

    def agent(code):
        try:
            return guard.is_agent_code(code)
        except Exception:   # noqa: BLE001 - a diagnostic never fails the suite itself.
            return False

    for holder in gc.get_objects():
        if len(found) >= limit:
            break
        try:
            if type(holder).__name__ == "function":
                code = holder.__code__
                if agent(code):
                    owners = [type(r).__name__ for r in gc.get_referrers(holder)][:4]
                    found.append("function %s.%s (%s) held by %s" % (
                        getattr(holder, "__module__", "?"), getattr(holder, "__qualname__", "?"),
                        code.co_filename, owners))
            elif type(holder).__name__ in ("frame", "generator", "coroutine") and agent(
                    getattr(holder, "f_code", None) or getattr(holder, "gi_code", None) or
                    getattr(holder, "cr_code", None)):
                found.append("%s %r" % (type(holder).__name__, holder)[:160])
            elif isinstance(holder, (dict, list, tuple)):
                values = holder.values() if isinstance(holder, dict) else holder
                for value in values:
                    if type(value).__name__ == "code" and agent(value):
                        keys = list(holder)[:5] if isinstance(holder, dict) else len(holder)
                        found.append("%s (%s) holds code %s %s" % (type(holder).__name__, keys, value.co_name,
                                                                  value.co_filename))
                        break
        except Exception:   # noqa: BLE001 - a diagnostic never fails the suite itself.
            continue
    return {"counts": counts, "agent_code_holders": found}


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
    network_refusals = []   # (text, what) of every network refusal any case meets, checked after the network cases.

    def run(source, _keep_namespace=False, **names):
        namespace = {"__name__": "__angelo_script__", "bpy": bpy, "functools": functools, "os": os,
                     "KEY": str(key), "STATE": str(state), "STARTUP": str(startup), "MARKER": str(MARKER), **names}
        from confinement_attacks_admit import admit
        admit("suite", source)
        try:
            # What the checks inspect is read while confined (the namespace is cleared there). The namespace is
            # returned only to a check that asks (the leftovers case, which clears it before the final check): a
            # result a check keeps (``plain = run(...)``) would otherwise keep the script's code alive, and the
            # hot-path "free" checks would read the suite's own reference as a leak (it happened three times:
            # ``attempt``, ``outcome``, and the integrator's ``plain`` holding ``Suite.execute``).
            findings, kept = script_confinement.run_confined(source, namespace, None, "suite", finish=dict)
            result = {"refused": None, "findings": findings}
            if _keep_namespace:
                result["namespace"] = kept
            del kept
            return result
        except script_confinement.ScriptConfined as error:
            if error.reason == script_confinement.NETWORK:
                network_refusals.append((str(error), getattr(error, "what", "")))
            return {"refused": error.reason, "what": getattr(error, "what", "")}
        except Exception as error:   # noqa: BLE001 - reported as the check's detail.
            return {"refused": None, "error": repr(error)}

    import _bpy
    # The count every hot path reads first (BLI_fileops_confine_open): zero with no thread confined, set while a
    # script runs, zero again after it (a leak would bring the per-set cost back for the rest of the session).
    check("hot_paths_free_before_scripts", _bpy._angelo_guard.confine_open() == 0, _bpy._angelo_guard.confine_open())
    inside = {}
    run("inside['open'] = confine_open()", inside=inside, confine_open=_bpy._angelo_guard.confine_open)
    check("hot_paths_ask_while_a_script_runs", (inside.get("open") or 0) >= 1, inside)
    gc.collect()   # (The script's code goes when nothing holds it; a cycle through its namespace needs a collection.)
    check("hot_paths_free_after_a_script", _bpy._angelo_guard.confine_open() == 0, _bpy._angelo_guard.confine_open())
    # A function the script left where the region's end can't replace it (a tuple: 2.0.0's own example of an
    # immutable holder), called later by Angelo's code at depth zero, with no driver in the file: only the stack test
    # confines it, so the count must still be set while it exists, and the gate it reaches must still refuse.
    held = {}
    run("def leftover():\n    return open(KEY).read()\nheld['leftover'] = (leftover,)", held=held)
    gc.collect()
    check("hot_paths_ask_while_script_code_lives", _bpy._angelo_guard.confine_open() >= 1,
          _bpy._angelo_guard.confine_open())
    try:
        read = held["leftover"][0]()
        check("leftover_code_confined_after_region", False, "read the private key: %d characters" % len(read))
    except script_confinement.ScriptConfined as error:
        check("leftover_code_confined_after_region", error.reason == PRIVATE, error.reason)
    except Exception as error:   # noqa: BLE001 - reported as the check's detail.
        check("leftover_code_confined_after_region", False, repr(error))
    held.clear()
    gc.collect()
    check("hot_paths_free_once_script_code_is_gone", _bpy._angelo_guard.confine_open() == 0,
          count_holders())
    # Code renamed to the agent's name after it was made (``_imp._fix_co_filename`` through importlib's own
    # ``_compile_bytecode``, which a script may call; BREAKIT script-isolation-review-fable-2 A2-MUST-1): code made
    # outside any script under an installed module's name, handed to the script as marshalled bytes. Renamed, it is
    # agent code; a function made from it and left in a tuple must still be refused when Angelo calls it later.
    import marshal
    installed = compile("def leak(path):\n    return open(path).read()\n", functools.__file__, "exec")
    renamed = {}
    attempt = run(
        "import importlib._bootstrap_external as e\n"
        "code = e._compile_bytecode(DATA, 'm', 'x', '<angelo-reviewed-script>')\n"
        "space = {'__builtins__': __builtins__}\n"
        "exec(code, space)\n"
        "renamed['leak'] = (space['leak'],)\n", DATA=marshal.dumps(installed), renamed=renamed)
    refused_rename = attempt.get("refused"), attempt.get("error")
    del attempt   # Its namespace holds the script's code; only `renamed` may keep the function alive below.
    gc.collect()
    if "leak" in renamed:
        try:
            read = renamed["leak"][0](str(key))
            check("renamed_code_confined_after_region", False, "read the private key: %d characters" % len(read))
        except script_confinement.ScriptConfined as error:
            check("renamed_code_confined_after_region", error.reason == PRIVATE, error.reason)
        except Exception as error:   # noqa: BLE001 - reported as the check's detail.
            check("renamed_code_confined_after_region", False, repr(error))
    else:   # The rename itself was refused: nothing was left to call, which is confined too.
        check("renamed_code_confined_after_region", refused_rename[0] is not None, refused_rename)
    renamed.clear()
    gc.collect()
    # (2.0.0 and b24a865f3c1 have no ``confine_counts``: -1 there, so the check below is red on them too.)
    counts = getattr(_bpy._angelo_guard, "confine_counts", lambda: {"states": -1, "codes": -1, "always": None})
    # Renamed code dropped in a loop, the read tried on every pass (BREAKIT break-it-confine-count-14a2d702b19
    # SHOULD-1, A2-MUST-1's attack as written): with one signed sum each drop took one away until it read exactly
    # zero inside the running script, and a read then went through. Each drop must give back only what was counted,
    # so every try stays refused, and no accounting error is ever made (``always`` stays False: the clamp at zero
    # would hide one in ``codes``).
    looped = {"refused": []}
    run("import importlib._bootstrap_external as e\n"
        "for _ in range(64):\n"
        "    try:\n"
        "        looped['read'] = len(open(KEY).read())\n"
        "        break\n"
        "    except BaseException as error:\n"
        "        looped['refused'].append(getattr(error, 'reason', repr(error)))\n"
        "        e._compile_bytecode(DATA, 'm', 'x', '<angelo-reviewed-script>')\n"
        "looped['counts'] = counts()\n",
        DATA=marshal.dumps(installed), looped=looped, counts=counts)
    gc.collect()
    after = counts()
    check("rename_loop_keeps_the_script_confined",
          "read" not in looped and len(looped["refused"]) == 64 and set(looped["refused"]) == {PRIVATE},
          {"read": looped.get("read"), "refused": sorted(set(looped["refused"])), "tries": len(looped["refused"])})
    check("rename_loop_makes_no_accounting_error",
          looped.get("counts", {}).get("always") is False and after["always"] is False and after["states"] == 0,
          (looped.get("counts"), after))
    check("hot_paths_free_after_rename_scripts", _bpy._angelo_guard.confine_open() == 0, after)
    # Code nested deeper than any fixed bound (BREAKIT break-it-confine-count-14a2d702b19 MUST-1, in 2.0.0 too): the
    # taint walk stopped at depth 64 and let deeper code through, so a lambda 70 levels down, compiled under
    # ``<string>``, was neither agent code nor counted, and the region's end took it for installed code. Left in a
    # tuple or in a handler list, it must still be refused when Angelo's code calls it after the region. The class is
    # depth, not lambdas: a nested ``def`` chain is checked too.
    deep = {}
    run("ns = {'KEY': KEY, 'open': open}\n"
        "exec('f = ' + 'lambda *a: ' * 70 + 'open(KEY).read()', ns)\n"
        "g = ns['f']\n"
        "for _ in range(69):\n"
        "    g = g()\n"
        "deep['tuple'] = (g,)\n"
        "bpy.app.handlers.depsgraph_update_post.append(g)\n"
        "deep['handler'] = len(bpy.app.handlers.depsgraph_update_post) - 1\n"
        "src = ''.join(' ' * i + 'def f%d(*a):\\n' % i for i in range(66)) + ' ' * 66 + 'return open(KEY).read()\\n'\n"
        "src += ''.join(' ' * (i + 1) + 'return f%d\\n' % (i + 1) for i in reversed(range(65)))\n"
        "nd = {'KEY': KEY, 'open': open}\n"
        "exec(src, nd)\n"
        "h = nd['f0']\n"
        "for _ in range(65):\n"
        "    h = h()\n"
        "deep['defs'] = (h,)\n", deep=deep)
    gc.collect()
    handlers = bpy.app.handlers.depsgraph_update_post
    for carrier, call in (("tuple", lambda: deep["tuple"][0]()),
                          ("handler", lambda: handlers[deep["handler"]](None, None)),
                          ("defs", lambda: deep["defs"][0]())):
        name = "deep_code_confined_after_region_" + carrier
        if carrier not in deep:
            check(name, False, "the script did not leave it: %r" % sorted(deep))
            continue
        try:
            read = call()
            check(name, False, "read the private key: %d characters" % len(read))
        except script_confinement.ScriptConfined as error:
            check(name, error.reason == PRIVATE, error.reason)
        except Exception as error:   # noqa: BLE001 - reported as the check's detail.
            check(name, False, repr(error))
    if "handler" in deep:
        handlers.pop(deep["handler"])
    deep.clear()
    gc.collect()
    check("hot_paths_free_after_deep_scripts", _bpy._angelo_guard.confine_open() == 0, counts())
    # A script that raised: its traceback holds its frames, and so its code, only while the error object lives.
    raised = run("raise ValueError('a script error')")
    gc.collect()
    check("hot_paths_free_after_a_script_error", "error" in raised and _bpy._angelo_guard.confine_open() == 0,
          (raised, _bpy._angelo_guard.confine_open()))
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

    # An operator that runs while a job locks the interface (bl_options LOCK_BYPASS): Angelo's, an add-on's or
    # Blender's, never a script's. The same class without it still registers; Angelo's own code still may.
    operator = ("class Suite(bpy.types.Operator):\n"
                "    bl_idname = 'suite.{name}'\n    bl_label = 'Suite'\n    bl_options = {options}\n"
                "    def execute(self, context):\n        return {{'FINISHED'}}\n"
                "bpy.utils.register_class(Suite)\n")
    bypass = run(operator.format(name="lock_bypass", options="{'LOCK_BYPASS'}"))
    check("refused_lock_bypass_operator", "LOCK_BYPASS" in str(bypass.get("error") or bypass.get("refused"))
          and not hasattr(bpy.types, "SUITE_OT_lock_bypass"), bypass)
    macro = run("class SuiteMacro(bpy.types.Macro):\n    bl_idname = 'suite.lock_bypass_macro'\n"
                "    bl_label = 'Suite'\n    bl_options = {'LOCK_BYPASS'}\nbpy.utils.register_class(SuiteMacro)\n")
    check("refused_lock_bypass_macro", "LOCK_BYPASS" in str(macro.get("error") or macro.get("refused"))
          and not hasattr(bpy.types, "SUITE_OT_lock_bypass_macro"), macro)
    plain = run(operator.format(name="plain", options="{'REGISTER'}"))
    registered = getattr(bpy.types, "SUITE_OT_plain", None)
    check("plain_operator_registers", plain.get("error") is None and registered is not None, plain)
    if registered is not None:
        bpy.utils.unregister_class(registered)
    registered = plain = bypass = macro = None   # The script's class would stay alive to the final "free" check.

    class SuiteOwn(bpy.types.Operator):   # Not a script: the app's own code.
        bl_idname = "suite.own_lock_bypass"
        bl_label = "Suite"
        bl_options = {"LOCK_BYPASS"}

        def execute(self, context):
            return {"FINISHED"}
    bpy.utils.register_class(SuiteOwn)
    check("own_code_registers_lock_bypass", hasattr(bpy.types, "SUITE_OT_own_lock_bypass"))
    bpy.utils.unregister_class(SuiteOwn)

    # Once registered, an operator type's registration properties never change, through its own instance or any
    # other (the review of 86b9af0f149, M1): a confined script's operator giving itself LOCK_BYPASS, and a write
    # through the person's operator history to Blender's own operator, raise, and so does the same write from
    # Angelo's own code. A string (an add-on's dialog title in bl_label, or an empty bl_undo_group) stays upstream's
    # silent no-op, for add-ons that set one (review 2, SHOULD-2), and is never written. Registration still sets them.
    own = {}
    run("class SuiteSelf(bpy.types.Operator):\n"
        "    bl_idname = 'suite.self_bypass'\n    bl_label = 'Suite'\n    bl_options = {'REGISTER'}\n"
        "    def execute(self, context):\n"
        "        for name, value in (('options', {'LOCK_BYPASS'}), ('label', 'Other'), ('undo_group', 'x')):\n"
        "            try:\n                setattr(self, 'bl_' + name, value)\n                out[name] = 'no error'\n"
        "            except AttributeError:\n                out[name] = 'refused'\n"
        "        out['flags'] = sorted(self.path_resolve('bl_options'))\n"
        "        out['strings'] = (self.path_resolve('bl_label'), self.path_resolve('bl_undo_group'))\n"
        "        return {'FINISHED'}\n"
        "bpy.utils.register_class(SuiteSelf)\nbpy.ops.suite.self_bypass()\n", out=own)
    check("refused_options_on_own_instance", own.get("options") == "refused" and own.get("flags") == ["REGISTER"],
          own)
    check("strings_stay_a_silent_no_op_on_own_instance", own.get("label") == "no error"
          and own.get("undo_group") == "no error" and own.get("strings") == ("Suite", ""), own)
    if getattr(bpy.types, "SUITE_OT_self_bypass", None) is not None:
        bpy.utils.unregister_class(bpy.types.SUITE_OT_self_bypass)

    # Angelo's own call with undo, as the person's own click runs it (Python's default, undo off, raises the undo
    # depth, and a call inside it never enters the history): it enters the person's operator history.
    bpy.ops.mesh.primitive_cube_add("EXEC_DEFAULT", True)
    history = [op for op in bpy.context.window_manager.operators if op.bl_idname == "MESH_OT_primitive_cube_add"]
    check("history_instance_found", bool(history), [op.bl_idname for op in bpy.context.window_manager.operators])
    if history:
        before = sorted(history[-1].bl_options)
        through = {}
        run("op = [o for o in bpy.context.window_manager.operators\n"
            "      if o.bl_idname == 'MESH_OT_primitive_cube_add'][-1]\n"
            "try:\n    op.bl_options = set(op.bl_options) | {'LOCK_BYPASS'}\n    out['history'] = 'written'\n"
            "except AttributeError:\n    out['history'] = 'refused'\n", out=through)
        after = sorted(history[-1].bl_options)
        check("refused_options_through_history", through.get("history") == "refused" and after == before,
              (through, before, after))
        try:
            history[-1].bl_options = set(before) | {"LOCK_BYPASS"}
            unconfined = "written"
            history[-1].bl_options = set(before)   # Only reached where the write was allowed: put it back.
        except AttributeError:
            unconfined = "refused"
        check("refused_options_unconfined", unconfined == "refused" and sorted(history[-1].bl_options) == before,
              unconfined)

    leftovers = run(_keep_namespace=True, source=
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
    leftovers = namespace = timer = handlers = evil = None
    gc.collect()
    check("hot_paths_free_after_every_script", _bpy._angelo_guard.confine_open() == 0,
          count_holders())

    # No network through a native reader or writer, from a script or later from Angelo's own code (BREAKIT
    # reread-r210-1c2c4edaf48 M-1: FFmpeg's network protocols; confinement_network_cases.py).
    from confinement_network_cases import run_network_cases
    run_network_cases(run, check, script_confinement.NETWORK, skip)
    # No network through a link already in a readable folder (confinement_link_cases.py).
    from confinement_link_cases import run_link_cases
    run_link_cases(run, check, script_confinement.NETWORK, skip)
    # The stat family: no lookup through a link reaches the network; what it costs (break-it-confine-reparse M7).
    from confinement_lookup_cases import run_lookup_cases
    run_lookup_cases(run, check, script_confinement.NETWORK, skip, REPORT["numbers"])
    # A network refusal names nothing the script passed or reached (a path, a URL, a share, a host, a pipe), in its
    # text or on the exception: the agent reads the reason alone; a share goes to the person only
    # (doc/angelo/script_confinement.rst, "The network").
    check("network_refusals_name_nothing", bool(network_refusals) and all(
        text == script_confinement.NETWORK and not what for text, what in network_refusals),
        sorted(set(network_refusals))[:8])
    # No confined move carries a link to a new path; ordinary moves don't wait (head-7 M6, confinement_move_cases.py).
    from confinement_move_cases import run_move_cases
    run_move_cases(run, check, skip, REPORT["numbers"])
    # No program started through a sound server (JACK's ~/.jackdrc, PulseAudio's autospawn).
    from confinement_sound_server_cases import run_sound_server_cases
    run_sound_server_cases(run, check, script_confinement.PROGRAMS, STARTUP)
    gc.collect()

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
    # Last (confinement_mixed_name_cases.py says why): nested code under another name than its parent's, minted
    # with marshal (break-it-confine-depth-5dc512c07e1 MUST-A), and a 300-deep chain.
    from confinement_mixed_name_cases import run_mixed_name_cases
    run_mixed_name_cases(run, check, PRIVATE, script_confinement.ScriptConfined, gc)
    # A loader the script built, claiming an installed file (break-it-confine-fixes-b31e34840ca MUST-1).
    from confinement_loader_cases import run_loader_cases
    run_loader_cases(run, check, PRIVATE, script_confinement.GUARD, script_confinement.ScriptConfined, gc)


try:
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    main()
    REPORT["success"] = bool(REPORT["checks"]) and all(REPORT["checks"].values())
except Exception:   # noqa: BLE001 - the receipt says why.
    REPORT["error"] = traceback.format_exc()
(OUT / "script-confinement.json").write_text(json.dumps(REPORT, indent=2, default=str))
print(json.dumps(REPORT, default=str)[:4000])
# What could not be judged here, on its own line and whole (the report above is cut): a skip is never a pass.
for name, reason in sorted(REPORT["skipped"].items()):
    print("ANGELO_SCRIPT_CONFINEMENT_SKIPPED %s: %s" % (name, reason))
if REPORT["success"]:
    print("ANGELO_SCRIPT_CONFINEMENT_OK")
sys.exit(0 if REPORT["success"] else 1)
