# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a script leaves outside its own objects, for test_blender_confinement_escapes (Devin break-it, 2026-11-15:
G1, G4, G5, G6): the process's own state, Preferences and the keymap, and Python carried by the project. Each check
ports the review's probe; the suite passed with all four open, so these are what cover those routes.

* G1: the locale and the interpreter's process-wide settings: put back at the script's end; limits, priority,
  identity and fork callbacks: refused (the environment, the working folder and the file-creation mask are refused
  outright: escapes_sinks.py).
* G6: every Preferences and keymap write (properties, items, functions, operators): refused.
* G4: a driver expression that needs Python and a style module: refused; a simple expression still works; a file
  opened with auto-run on runs a driver's Python confined; a driver the script brought in is saved with its Python
  off; a library linked with code that runs when a file opens can't be saved and is removed.
* G5: a generator the script left, resumed later, can't save a text that runs when a file opens.
"""

import gc
import json
import locale
import os
import subprocess
import sys

import bpy

CHILD_ENV = ("ANGELO_PROBE_PLANTED", "ANGELO_PROBE_KEPT", "LD_PRELOAD", "PYTHONSTARTUP")


def _child_environment():
    """The C environment a program started now inherits (not `os.environ`, the mapping)."""
    code = "import json, os; print(json.dumps({k: os.environ.get(k) for k in %r}))" % (CHILD_ENV,)
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)
    return json.loads(done.stdout.strip().splitlines()[-1])


def _umask():
    value = os.umask(0o022)
    os.umask(value)
    return value


def process_state(run, check, plain):
    os.environ["ANGELO_PROBE_KEPT"] = "angelo"
    before = {"env": _child_environment(), "cwd": os.getcwd(), "umask": _umask(),
              "locale": locale.setlocale(locale.LC_ALL, None), "gc": (gc.isenabled(), gc.get_threshold()),
              "recursion": sys.getrecursionlimit(), "interval": sys.getswitchinterval(),
              "digits": sys.get_int_max_str_digits()}
    other_locale = next((name for name in ("C.UTF-8", "C", "POSIX", "en_US.UTF-8")
                         if name != locale.setlocale(locale.LC_NUMERIC, None)), "C")
    outcome = run("import os, gc, sys, locale\n"
                  "try:\n    locale.setlocale(locale.LC_NUMERIC, OTHER)\nexcept locale.Error:\n    pass\n"
                  "gc.disable()\ngc.set_threshold(0)\nsys.setrecursionlimit(54321)\nsys.setswitchinterval(0.25)\n"
                  "sys.set_int_max_str_digits(0)\n", PLAIN=str(plain), OTHER=other_locale)
    after = {"env": _child_environment(), "cwd": os.getcwd(), "umask": _umask(),
             "locale": locale.setlocale(locale.LC_ALL, None), "gc": (gc.isenabled(), gc.get_threshold()),
             "recursion": sys.getrecursionlimit(), "interval": sys.getswitchinterval(),
             "digits": sys.get_int_max_str_digits()}
    changed = {key: (before[key], after[key]) for key in before if before[key] != after[key]}
    check("process_state_put_back", outcome["refused"] is None and "error" not in outcome and changed == {},
          (outcome, changed))
    if sys.platform == "win32":
        return   # Limits, priority, identity: POSIX calls (Windows has no resource module, nice or setsid).
    refused = {}
    for name, source in (("nice", "import os\nos.nice(1)\n"),
                         ("setpriority", "import os\nos.setpriority(os.PRIO_PROCESS, 0, 5)\n"),
                         ("setrlimit", "import resource\nr = resource.getrlimit(resource.RLIMIT_CORE)\n"
                                       "resource.setrlimit(resource.RLIMIT_CORE, r)\n"),
                         ("setsid", "import os\nos.setsid()\n")):
        refused[name] = run(source)["refused"]
    check("process_changes_refused", all(reason is not None for reason in refused.values()), refused)
    # A callback the script registers to run at fork runs confined (the trampoline); installed modules' own (random,
    # threading, logging reseeding at fork) stay as they are.
    if sys.platform != "win32":
        holder = {}
        outcome = run("import os\nos.register_at_fork(after_in_child=CALLBACK)\nHELD['registered'] = True\n",
                      CALLBACK=lambda: None, HELD=holder)
        check("fork_callback_registered", outcome["refused"] is None and holder.get("registered"), outcome)


def preferences(run, check):
    prefs = bpy.context.preferences
    before = (prefs.filepaths.use_scripts_auto_execute, prefs.view.ui_scale, len(prefs.filepaths.script_directories))
    sources = {
        "auto_run": "bpy.context.preferences.filepaths.use_scripts_auto_execute = True\n",
        "ui_scale": "bpy.context.preferences.view.ui_scale = 1.7\n",
        "theme_item": "bpy.context.preferences.themes[0].view_3d.space.gradients.high_gradient[0] = 0.25\n",
        "script_directory": "bpy.context.preferences.filepaths.script_directories.new()\n",
        "addon_preferences": ("p = bpy.context.preferences.addons['angelo_agent'].preferences\n"
                              "name = next(n for n in p.bl_rna.properties.keys() if n != 'rna_type')\n"
                              "setattr(p, name, getattr(p, name))\n"),
        "keymap_item": ("km = bpy.context.window_manager.keyconfigs.user.keymaps[0]\n"
                        "km.keymap_items.new('script.python_file_run', 'F12', 'PRESS')\n"),
        "operator": "bpy.ops.preferences.autoexec_path_add()\n",
    }
    refused = {name: run(source)["refused"] for name, source in sources.items()}
    after = (prefs.filepaths.use_scripts_auto_execute, prefs.view.ui_scale, len(prefs.filepaths.script_directories))
    check("preferences_refused", all(reason is not None for reason in refused.values()) and before == after,
          (refused, before, after))


def _driver_file(path, expression):
    """A file the person made: an empty whose X location a scripted driver sets."""
    scene = bpy.data.scenes.new("probe_scene")
    ob = bpy.data.objects.new("probe", None)
    scene.collection.objects.link(ob)
    driver = ob.driver_add("location", 0).driver
    driver.type = 'SCRIPTED'
    driver.expression = expression
    bpy.data.libraries.write(str(path), {scene, ob}, fake_user=True)
    bpy.data.objects.remove(ob)
    bpy.data.scenes.remove(scene)


def _open_with_autorun(path):
    """A fresh app with auto-run on opens `path` and evaluates its driver: what the driver set."""
    code = ("import addon_utils, bpy, json\n"
            "addon_utils.enable('angelo_agent', default_set=False, persistent=False)\n"
            "bpy.ops.wm.open_mainfile(filepath=%r, load_ui=False, use_scripts=True)\n"
            "ob = bpy.data.objects['probe']\nscene = ob.users_scene[0]\n"
            "scene.frame_set(3)\nprint('CHILD ' + json.dumps({'x': ob.location[0]}))\n") % str(path)
    done = subprocess.run([bpy.app.binary_path, "--background", "--factory-startup", "--python-expr", code],
                          capture_output=True, text=True, timeout=600)
    for line in done.stdout.splitlines():
        if line.startswith("CHILD "):
            return json.loads(line[6:])["x"]
    return "no result: " + done.stdout[-400:] + done.stderr[-400:]


def drivers_and_modules(run, check, plain):
    sources = {
        "python_expression": ("ob = bpy.data.objects.new('drv', None)\nd = ob.driver_add('location', 0).driver\n"
                              "d.type = 'SCRIPTED'\nd.expression = \"__import__('os').getcwd() and 1\"\n"),
        "scripted_over_python": ("ob = bpy.data.objects.new('drv2', None)\nd = ob.driver_add('location', 0).driver\n"
                                 "d.type = 'AVERAGE'\nd.expression = '(lambda: 7)()'\nd.type = 'SCRIPTED'\n"),
        "style_module": ("m = bpy.context.view_layer.freestyle_settings.modules.new()\n"
                         "m.script = bpy.data.texts.new('style.py')\n"),
    }
    refused = {name: run(source)["refused"] for name, source in sources.items()}
    check("project_python_refused", all(reason is not None for reason in refused.values()), refused)
    simple = run("ob = bpy.data.objects.new('simple', None)\nbpy.context.scene.collection.objects.link(ob)\n"
                 "d = ob.driver_add('location', 0).driver\nd.type = 'SCRIPTED'\n"
                 "d.expression = 'var * 2 + sin(frame) * 0'\nv = d.variables.new()\nv.name = 'var'\n"
                 "v.type = 'SINGLE_PROP'\nv.targets[0].id = ob\nv.targets[0].data_path = 'location.y'\n")
    check("simple_driver_works", simple["refused"] is None and "error" not in simple, simple)

    marker = plain / "the-script-ran-at-open"
    marker.unlink(missing_ok=True)
    harmful, harmless = plain / "driver-harmful.blend", plain / "driver-harmless.blend"
    command = ('type nul > "%s"' if sys.platform == "win32" else "touch '%s'") % marker
    _driver_file(harmful, "__import__('os').system(%r) or 7" % command)
    _driver_file(harmless, "(lambda: 7)()")
    opened = {"harmful": _open_with_autorun(harmful), "harmless": _open_with_autorun(harmless)}
    check("driver_python_confined_at_open", not marker.exists() and opened["harmful"] != 7, (opened, marker.exists()))
    check("person_python_driver_works", opened["harmless"] == 7, opened)
    saved = plain / "agent-driver.blend"
    outcome = run("with bpy.data.libraries.load(SOURCE, link=False) as (src, dst):\n"
                  "    dst.objects = ['probe']\n"
                  "bpy.context.scene.collection.objects.link(dst.objects[0])\n"
                  "bpy.ops.wm.save_as_mainfile(filepath=TARGET, copy=True)\n", SOURCE=str(harmless),
                  TARGET=str(saved))
    check("brought_in_driver_saved_inert", outcome["refused"] is None and _open_with_autorun(saved) == 0,
          (outcome, saved.exists()))


def generator_autorun(run, check, plain, holder, autorun_blend):
    """The review's probe: a generator the script left appends a text that runs at load and saves, later."""
    target = plain / "generator-saved.blend"
    holder.generators = []
    run("def later():\n"
        "    with bpy.data.libraries.load(SOURCE, link=False) as (src, dst):\n"
        "        dst.texts = list(src.texts)\n"
        "    for t in bpy.data.texts:\n"
        "        t.use_fake_user = True\n"
        "    yield [t.name for t in bpy.data.texts]\n"
        "    bpy.ops.wm.save_as_mainfile(filepath=TARGET, copy=True)\n"
        "    yield 'saved'\n"
        "HOLDER.generators.append(later())\n", SOURCE=str(autorun_blend), TARGET=str(target), HOLDER=holder)
    steps = []
    for generator in holder.generators:
        for _ in range(2):
            try:
                steps.append(str(next(generator))[:80])
            except Exception as error:   # noqa: BLE001 - a refusal is a pass.
                steps.append(repr(error)[:120])
    saved = {}
    if target.exists():
        with bpy.data.libraries.load(str(target), link=False) as (src, dst):
            dst.texts = list(src.texts)
        saved = {t.name: t.use_module for t in dst.texts if t is not None}
        for t in dst.texts:
            if t is not None:
                bpy.data.texts.remove(t)
    check("generator_saved_no_load_time_text", not any(saved.values()), (steps, saved))


def linked_library(run, check, plain, autorun_blend):
    """A text that runs at load, linked: not kept on entry (its code would come back from the library at every open),
    so nothing that saves afterwards carries it."""
    outcome = run("with bpy.data.libraries.load(SOURCE, link=True) as (src, dst):\n"
                  "    dst.texts = list(src.texts)\n"
                  "HELD['linked'] = [t is None for t in dst.texts]\n"
                  "bpy.ops.wm.save_as_mainfile(filepath=TARGET, copy=True)\n", SOURCE=str(autorun_blend),
                  TARGET=str(plain / "linked.blend"), HELD=(held := {}))
    still = [t.name for t in bpy.data.texts if t.library is not None and t.use_module and
             bpy.path.abspath(t.library.filepath) == str(autorun_blend)]
    check("linked_code_library_not_kept", still == [] and (outcome["refused"] is not None or
                                                           all(held.get("linked") or [False])),
          (outcome, held, still))


def closures(run, check):
    """An installed function's closure (where installed code keeps an original) is not read while confined, by the
    script or a helper it calls; a script's own closures are (pipeline-gates, G3)."""
    refused = {name: run(source)["refused"] for name, source in (
        ("direct", "import functools\ncells = functools.wraps.__closure__\n"),
        ("helper", "import functools, inspect\nfound = inspect.getclosurevars(functools.wraps)\n"),
        ("getattr", "import functools, operator\ncells = operator.attrgetter('__closure__')(functools.wraps)\n"))}
    own = run("def outer():\n    x = 41\n    def inner():\n        return x + 1\n    return inner\n"
              "assert outer().__closure__[0].cell_contents == 41\n")
    check("installed_closures_refused", all(reason is not None for reason in refused.values()), refused)
    check("own_closures_readable", own["refused"] is None and "error" not in own, own)


def without_the_add_on(check):
    """A process that runs agent scripts without the add-on registered (a worker, a headless run) still runs them,
    confined: the confinement installs itself on the first run (pipeline-gates, Windows build4: "confinement
    unavailable" refused every script there)."""
    import addon_utils
    folders = [path for path in addon_utils.paths() if path.endswith("addons_core")]
    code = ("import sys, json\nsys.path[:0] = %r\n"
            "from confinement_attacks_admit import admit\nfrom angelo_agent import script_confinement\n"
            "src = 'import _bpy\\nresult = _bpy._angelo_guard.confined()\\n'\n"
            "admit('child', src)\nns = {'__name__': '__angelo_script__'}\n"
            "_, got = script_confinement.run_confined(src, ns, None, 'child', finish=lambda n: n.get('result'))\n"
            "print('CHILD ' + json.dumps({'confined': got}))\n") % ([os.path.dirname(os.path.abspath(__file__))]
                                                                     + folders,)
    done = subprocess.run([bpy.app.binary_path, "--background", "--factory-startup", "--python-expr", code],
                          capture_output=True, text=True, timeout=600)
    found = [line for line in done.stdout.splitlines() if line.startswith("CHILD ")]
    result = json.loads(found[-1][6:]) if found else {"output": (done.stdout + done.stderr)[-600:]}
    check("runs_without_the_add_on", result.get("confined") is True, result)


def bytecode_cache(run, check):
    """An installed module whose bytecode Python cached in the bytecode-cache folder (a release sets
    PYTHONPYCACHEPREFIX; a second launch finds it there) imports in a confined script: an add-on in the user scripts
    folder, or Angelo's own modules run from a checkout (w42fix: scene_queries refused at its cached .pyc). The
    shipped install's modules read their own __pycache__ instead. A script can't write in the cache folder."""
    import importlib
    import importlib.util
    import py_compile
    prefix = sys.pycache_prefix
    if not prefix:
        check("bytecode_cache_imports", True, "no bytecode-cache folder in this run")
        return
    root = bpy.utils.user_resource("SCRIPTS", path="modules", create=True)
    source = os.path.join(root, "angelo_fixture_cached.py")
    with open(source, "w") as file:
        file.write("VALUE = 41 + 1\n")
    if root not in sys.path:
        sys.path.insert(0, root)
    importlib.invalidate_caches()
    cached = py_compile.compile(source, cfile=importlib.util.cache_from_source(source), doraise=True)
    imported = run("import angelo_fixture_cached\nassert angelo_fixture_cached.VALUE == 42\n")
    written = run("open(PATH, 'wb').write(b'planted')\n", PATH=os.path.join(prefix, "angelo-planted.pyc"))
    check("bytecode_cache_imports", imported["refused"] is None and "error" not in imported,
          (cached, imported))
    check("bytecode_cache_not_writable", written["refused"] is not None, written)


def run_all(run, check, plain, holder, autorun_blend):
    bytecode_cache(run, check)
    without_the_add_on(check)
    closures(run, check)
    process_state(run, check, plain)
    preferences(run, check)
    drivers_and_modules(run, check, plain)
    generator_autorun(run, check, plain, holder, autorun_blend)
    linked_library(run, check, plain, autorun_blend)
    import escapes_after   # Devin break-it Phase B 2026-11-15: MUST-1, SHOULD-1, SHOULD-2.
    escapes_after.run_all(run, check, plain, holder, autorun_blend, _open_with_autorun, _driver_file)
    import escapes_sinks   # Devin break-it Phase C 2026-11-15: MUST-4, SHOULD-1 (and MUST-1..3's sink).
    escapes_sinks.run_all(run, check, plain)
    import escapes_review   # The coordinator's review of piece 1: environment, steered imports, generator steps.
    escapes_review.run_all(run, check, plain)
    import escapes_review2   # The re-read of pieces 2+3: steered definitions, provenance, import state, records.
    escapes_review2.run_all(run, check, plain)
    import escapes_loading   # Names a script planted in an installed module's namespace before its body ran.
    escapes_loading.run_all(run, check)
    import escapes_review3   # The reviewer's third read: remapped names, finalize records, seeding, packages.
    escapes_review3.run_all(run, check)
    import escapes_review4   # The reviewer's fourth read: nothing runs as a region settles, records, namespaces.
    escapes_review4.run_all(run, check)
    import escapes_angelo_io   # Angelo's own network, TLS and logging paths against a script's changes.
    escapes_angelo_io.run_all(run, check)
    import escapes_review5   # The reviewer's fifth read: gained modules by their loader, logging after a step.
    escapes_review5.run_all(run, check)
    import escapes_review5_angelo   # Angelo's package and frames closed; cleanups, signals, credentials by C's facts.
    escapes_review5_angelo.run_all(run, check)
    import escapes_review5_sinks   # Every exit and cleanup path leaves a target a script planted in its state.
    escapes_review5_sinks.run_all(run, check)
    import escapes_review6   # The reviewer's sixth read: hooks threading gives new threads.
    escapes_review6.run_all(run, check, plain)
    import escapes_review7_links   # The reviewer's seventh read, item 4: no link a script makes for Angelo to follow.
    escapes_review7_links.run_all(run, check, plain)
    import escapes_review7_callbacks   # Item 4's NITs: callbacks other threads' data reaches, Angelo's own only.
    escapes_review7_callbacks.run_all(run, check)
    import escapes_p10c4_export   # Piece 10 MUST-A, export group: renders, views, jobs, Studio by C's facts.
    escapes_p10c4_export.run_all(run, check, plain)
    import escapes_engine_children   # Piece 10 MUST-A, engine group: a write-back child's targets asked of C first.
    escapes_engine_children.run_all(run, check, plain)
    import escapes_review7   # The review of piece 10: no folder vouched, nothing kept, hard links, writers.
    escapes_review7.run_all(run, check, plain)
    import escapes_review8   # Its re-review's SHOULDs: a script's file browser, typed paths per writer, Windows forms.
    escapes_review8.run_all(run, check, plain)
    import escapes_review9   # Their re-read: output paths a script sets are recorded; writers in them; templates now.
    escapes_review9.run_all(run, check, plain)
    import escapes_review10   # Re-read 4: ungated writers (multires, Save Sequence), typed tags, browser functions.
    escapes_review10.run_all(run, check, plain)
    # Blender's writers (renders, composites, bakes) are test_blender_confinement_writers: it may take a GPU path.
    import escapes_provenance   # The record of what scripts change: links, every writer, fail closed, pruning.
    escapes_provenance.run_all(run, check, plain)
    import escapes_trust   # Phase C MUST-1..3: Python a file carries runs only as the person trusted it (last: it
    escapes_trust.run_all(run, check, plain)   # ends by opening another file).
