# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a script leaves for Angelo's own later work, for test_blender_confinement_escapes (Devin break-it Phase B,
2026-11-15: MUST-1, SHOULD-1, SHOULD-2). Each check ports the review's probe and failed before its fix.

* MUST-1: Python that enters the project from confined code by any route (append, link, load), in a region or from a
  generator the script left and resumed later, is inert on entry: a text that runs at load stops running at load,
  a driver's Python is off, a linked carrier is not kept.
* SHOULD-1: signal dispositions, the signal mask, the wakeup descriptor, the default socket timeout, the warning
  filters and the time zone a script sets don't outlive it; what can't be put back is refused.
* SHOULD-2: `sys.modules` is as the script found it: a module object it planted (a `tarfile` whose `is_tarfile` is
  `shutil.rmtree`), a `None` that blocks an import, an installed module under another's name are gone, a replaced
  entry is back, and an import of installed code stays.
"""

import importlib
import importlib.util
import os
import signal
import socket
import subprocess
import sys
import time
import warnings

import bpy

# Installed modules nothing in a fresh app needs yet: free names for the plants.
CANDIDATES = ("tabnanny", "quopri", "colorsys", "wave", "sched", "netrc", "fileinput", "shelve", "cmd", "mailbox",
              "imghdr", "sndhdr", "nntplib", "poplib", "pydoc_data", "plistlib", "graphlib", "tomllib")


def _free_names(count):
    names = [name for name in CANDIDATES if name not in sys.modules and importlib.util.find_spec(name) is not None]
    return names[:count]


def _settled(probe):
    """Run some bytecode so pending calls (where code outside a region is settled) get their turn."""
    for _ in range(2000):
        probe = probe
    return probe


def _real(name):
    module = sys.modules.get(name)
    spec = getattr(module, "__spec__", None)
    return module is not None and getattr(module, "__name__", None) == name and spec is not None and \
        getattr(spec, "origin", None) not in (None, "")


def modules_planted(run, check):
    names = _free_names(6)
    if len(names) < 6:
        check("modules_planted_names", False, names)
        return
    fresh, blocked, other, imported, later, later_import = names
    json_before = sys.modules["json"]
    outcome = run("import sys, types, shutil\n"
                  "m = types.ModuleType(FRESH)\nm.is_tarfile = shutil.rmtree\nsys.modules[FRESH] = m\n"
                  "sys.modules[BLOCKED] = None\n"
                  "sys.modules[OTHER] = shutil\n"
                  "fake = types.SimpleNamespace(loads=shutil.rmtree, dumps=repr)\nsys.modules['json'] = fake\n"
                  "import importlib\nimportlib.import_module(IMPORTED)\n",
                  FRESH=fresh, BLOCKED=blocked, OTHER=other, IMPORTED=imported)
    after = {
        "fresh": fresh not in sys.modules or _real(fresh),
        "blocked": sys.modules.get(blocked, "absent") is not None,
        "other": sys.modules.get(other) is not sys.modules.get("shutil"),
        "json": sys.modules["json"] is json_before,
        "import_kept": _real(imported),
    }
    reimported = {}
    for name in (fresh, blocked, other):
        try:
            module = importlib.import_module(name)
            reimported[name] = getattr(module, "__name__", None) == name and getattr(module, "__file__", None)
        except Exception as error:   # noqa: BLE001 - recorded.
            reimported[name] = repr(error)[:80]
    check("modules_planted_removed", outcome["refused"] is None and all(after.values()) and
          all(isinstance(value, str) and os.path.isfile(value) for value in reimported.values()),
          (outcome, after, reimported))

    # A generator the script left plants after its run: settled the next time the main thread runs Python.
    holder = {}
    run("import sys, types, shutil\n"
        "def later():\n"
        "    m = types.ModuleType(LATER)\n    m.is_tarfile = shutil.rmtree\n    sys.modules[LATER] = m\n"
        "    import importlib\n    importlib.import_module(LATER_IMPORT)\n"
        "    yield 'planted'\n"
        "HELD['generator'] = later()\n", LATER=later, LATER_IMPORT=later_import, HELD=holder)
    step = None
    try:
        step = next(holder.pop("generator"))
    except Exception as error:   # noqa: BLE001 - a refusal is a pass too.
        step = repr(error)[:120]
    _settled(step)
    module = importlib.import_module(later)
    check("modules_planted_outside_region_removed",
          getattr(module, "is_tarfile", None) is None and os.path.isfile(getattr(module, "__file__", "") or "") and
          (later_import not in sys.modules or _real(later_import)),
          (step, getattr(module, "__file__", None), sys.modules.get(later_import)))


def _child_signals():
    """Linux: the blocked and ignored signal sets a program Angelo starts now inherits."""
    done = subprocess.run(["/bin/sh", "-c", "grep -E '^Sig(Blk|Ign)' /proc/self/status"], capture_output=True,
                          text=True, timeout=60)
    return done.stdout.strip()


def signals_and_settings(run, check):
    numbers = [getattr(signal, name) for name in ("SIGTERM", "SIGINT", "SIGHUP", "SIGUSR1", "SIGUSR2")
               if hasattr(signal, name)]
    posix = hasattr(signal, "pthread_sigmask")
    linux = sys.platform.startswith("linux")

    def state():
        return {"handlers": {int(n): signal.getsignal(n) for n in numbers},
                "mask": sorted(int(n) for n in signal.pthread_sigmask(signal.SIG_BLOCK, [])) if posix else None,
                "child": _child_signals() if linux else None,
                "timeout": socket.getdefaulttimeout(),
                "filters": list(warnings.filters), "action": warnings.defaultaction,
                "zone": time.tzname, "zone_env": os.environ.get("TZ")}

    before = state()
    held = {}
    outcome = run("import signal, socket, warnings, os, time\n"
                  "held = HELD.setdefault('signals', {})\n"
                  "for n in NUMBERS:\n"
                  "    try:\n        signal.signal(n, signal.SIG_IGN)\n        held[n] = 'set'\n"
                  "    except Exception as error:\n        held[n] = type(error).__name__\n"
                  "if hasattr(signal, 'pthread_sigmask'):\n"
                  "    signal.pthread_sigmask(signal.SIG_BLOCK, [n for n in NUMBERS if n != signal.SIGINT])\n"
                  "socket.setdefaulttimeout(0.001)\n"
                  "warnings.simplefilter('error')\nwarnings.filters.insert(0, ('ignore', None, Warning, None, 0))\n"
                  "warnings.defaultaction = 'error'\n"
                  "if hasattr(time, 'tzset'):\n    os.environ['TZ'] = 'Asia/Kathmandu'\n    time.tzset()\n",
                  NUMBERS=numbers, HELD=held)
    after = state()
    changed = {key: (before[key], after[key]) for key in before if before[key] != after[key]}
    check("signals_and_settings_put_back", "error" not in outcome and changed == {}, (outcome, held, changed))
    if posix:
        refused = {name: run(source)["refused"] for name, source in (
            ("alarm", "import signal\nsignal.alarm(100)\n"),
            ("setitimer", "import signal\nsignal.setitimer(signal.ITIMER_REAL, 100)\n"),
            ("siginterrupt", "import signal\nsignal.siginterrupt(signal.SIGTERM, True)\n"),
            ("faulthandler_disable", "import faulthandler\nfaulthandler.disable()\n"))}
        check("signal_timers_refused", all(reason is not None for reason in refused.values()) and
              signal.getitimer(signal.ITIMER_REAL)[0] == 0, refused)
    # The same from a generator the script left, resumed outside a region: refused (no region end would put it back).
    holder = {}
    run("import signal\n"
        "def later():\n    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n    yield 'set'\n"
        "HELD['generator'] = later()\n", HELD=holder)
    try:
        step = next(holder.pop("generator"))
    except Exception as error:   # noqa: BLE001 - a refusal is the pass.
        step = type(error).__name__
    check("signal_outside_region_refused", signal.getsignal(signal.SIGTERM) == before["handlers"][int(signal.SIGTERM)],
          (step, signal.getsignal(signal.SIGTERM)))
    signal.signal(signal.SIGTERM, before["handlers"][int(signal.SIGTERM)] or signal.SIG_DFL)


def carriers_on_entry(run, check, plain, holder, autorun_blend, open_with_autorun, driver_file):
    """MUST-1: what a generator the script left appends after its run is inert on entry, and stays so in the person's
    later save; a linked carrier is not kept."""
    texts_before = {t.name for t in bpy.data.texts}
    holder.generators = []
    for ob in [ob for ob in bpy.data.objects if ob.name.startswith("probe")]:
        bpy.data.objects.remove(ob)   # Earlier checks' (the file's object must be named "probe").
    harmless = plain / "entry-driver.blend"
    driver_file(harmless, "(lambda: 7)()")
    run("def later():\n"
        "    with bpy.data.libraries.load(SOURCE, link=False) as (src, dst):\n"
        "        dst.texts = list(src.texts)\n"
        "    with bpy.data.libraries.load(DRIVER, link=False) as (src, dst):\n"
        "        dst.objects = ['probe']\n"
        "    bpy.context.scene.collection.objects.link(dst.objects[0])\n"
        "    yield 'appended'\n"
        "HOLDER.generators.append(later())\n", SOURCE=str(autorun_blend), DRIVER=str(harmless), HOLDER=holder)
    steps = []
    for generator in holder.generators:
        try:
            steps.append(str(next(generator))[:80])
        except Exception as error:   # noqa: BLE001 - recorded.
            steps.append(repr(error)[:120])
    live = {t.name: t.use_module for t in bpy.data.texts if t.name not in texts_before}
    later = plain / "person-saves-later.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(later), copy=True)   # The person, unconfined.
    saved = {}
    with bpy.data.libraries.load(str(later), link=False) as (src, dst):
        dst.texts = [name for name in src.texts if name not in texts_before]
    saved = {t.name: t.use_module for t in dst.texts if t is not None}
    for t in list(dst.texts) + [t for t in bpy.data.texts if t.name not in texts_before]:
        if t is not None and t.name in bpy.data.texts:
            bpy.data.texts.remove(t)
    check("generator_appended_text_inert", steps == ["appended"] and live and not any(live.values()) and
          not any(saved.values()), (steps, live, saved))
    check("generator_appended_driver_inert", open_with_autorun(later) == 0, steps)
    for ob in [ob for ob in bpy.data.objects if ob.name.startswith("probe")]:
        bpy.data.objects.remove(ob)


def addon_after_script(run, check):
    """An installed add-on a script imported and called is installed code afterwards: enabling it (register) and
    disabling it (unregister, keymap removal) after the run is never judged the agent's."""
    import addon_utils
    from angelo_agent import script_confinement
    outcome = run("import node_wrangler\nfrom node_wrangler.utils import constants\n"
                  "result = (node_wrangler.__name__, constants.nice_hotkey_name('BACK_SPACE'))\n")
    errors = []
    enabled = addon_utils.enable("node_wrangler", default_set=False, persistent=False, handle_error=errors.append)
    disabled = None
    try:
        addon_utils.disable("node_wrangler", default_set=False, handle_error=errors.append)
        disabled = "node_wrangler" not in {mod.__name__ for mod in addon_utils.modules() if
                                           addon_utils.check(mod.__name__)[1]}
    except Exception as error:   # noqa: BLE001 - the failure under test.
        errors.append(error)
    check("addon_unregister_after_script", outcome["refused"] is None and "error" not in outcome and
          enabled is not None and disabled and not errors and not script_confinement.confined(),
          (outcome, enabled, disabled, [repr(e)[:200] for e in errors]))


def native_stack(run, check):
    """A callable a script left that re-enters itself through C alone (a `__hash__` that is an `lru_cache` keyed on
    the object) ends in RecursionError when Angelo's code reaches it, never a crash: on a thread with a small stack
    too, where CPython's recursion count is reached long after the stack is gone (macOS SIGSEGV, 2026-11-15)."""
    import threading
    held = {}
    outcome = run("import functools\n"
                  "class H:\n    __hash__ = functools.lru_cache(maxsize=None)(lambda self: 1)\n"
                  "HELD['h'] = H()\n", HELD=held)
    result = {}

    def angelo_hashes(where):
        try:
            result[where] = hash(held.get("h"))
        except RecursionError:
            result[where] = "RecursionError"
        except Exception as error:   # noqa: BLE001 - recorded.
            result[where] = "%s: %s" % (type(error).__name__, str(error)[:200])

    was = threading.stack_size(1024 * 1024)
    try:
        worker = threading.Thread(target=angelo_hashes, args=("thread",))
        worker.start()
        worker.join()
    finally:
        threading.stack_size(was)
    angelo_hashes("main")
    # An exception, never a crash or a value: RecursionError, or the TypeError CPython's `slot_tp_hash` turns any
    # error in looking up `__hash__` into ("unhashable type") when the check fires while binding it.
    ended = all(str(result.get(where, "")).startswith(("RecursionError", "TypeError: unhashable"))
                for where in ("thread", "main"))
    check("native_stack_recursion_ends", outcome["refused"] is None and ended,
          (outcome, result))


def windows_entries(run, check, key):
    """Windows' native entry points outside the descriptor checks: a raw handle to a private file
    (`_winapi.CreateFile` + `ReadFile`), another process (`OpenProcess`), every process's command line (`_wmi`),
    a local service's named pipe (`_overlapped.ConnectPipe`), the process's error mode (`msvcrt.SetErrorMode`)."""
    if sys.platform != "win32":
        return
    rows = []
    sources = {
        "winapi_createfile_private": ("import _winapi\nh = _winapi.CreateFile(KEY, 0x80000000, 1, 0, 3, 0, 0)\n"
                                      "ROWS.append(_winapi.ReadFile(h, 100))\n"),
        "winapi_openprocess": "import _winapi, os\n_winapi.OpenProcess(0x1000, False, os.getppid())\n",
        "wmi_command_lines": ("import _wmi\nROWS.append(_wmi.exec_query("
                              "'SELECT CommandLine FROM Win32_Process'))\n"),
        "overlapped_connect_pipe": "import _overlapped\n_overlapped.ConnectPipe(r'\\\\.\\pipe\\angelo-probe')\n",
        "msvcrt_error_mode": "import msvcrt\nmsvcrt.SetErrorMode(0)\n",
    }
    refused = {name: run(source, KEY=str(key), ROWS=rows) for name, source in sources.items()}
    check("windows_entries_refused", all(o["refused"] is not None for o in refused.values()) and rows == [],
          (refused, [str(row)[:40] for row in rows]))
    # Pipes, judged by their arguments whoever calls: the pair a process makes for itself (every pool's change
    # notifier) works; a remote name, a server for other processes (a listener: unlimited instances, at a
    # script-chosen name or not) and a remote name given straight to _winapi are refused, through multiprocessing's
    # own call path too.
    pair = run("import multiprocessing.connection as c\na, b = c.Pipe()\na.send(7)\nROWS.append(b.recv())\n"
               "a.close()\nb.close()\n", ROWS=rows)
    check("windows_local_pipe_pair_works", pair["refused"] is None and "error" not in pair and rows[-1:] == [7],
          (pair, rows))
    pipes = {
        "listener_remote": ("import multiprocessing.connection as c\n"
                            "c.Listener(r'\\\\angelo-remote\\pipe\\angelo-probe', family='AF_PIPE')\n"),
        "listener_local": ("import multiprocessing.connection as c\n"
                           "c.Listener(r'\\\\.\\pipe\\angelo-script-chosen', family='AF_PIPE')\n"),
        "listener_default": "import multiprocessing.connection as c\nc.Listener(family='AF_PIPE')\n",
        "unc_name": ("import _winapi\n_winapi.CreateNamedPipe(r'\\\\?\\UNC\\angelo-remote\\pipe\\x', "
                     "0x80003, 0, 1, 0, 0, 0, 0)\n"),
        "existing_name": ("import _winapi\n_winapi.CreateNamedPipe(r'\\\\.\\pipe\\angelo-shared', "
                          "0x00000003, 0, 1, 0, 0, 0, 0)\n"),
    }
    refused = {name: run(source)["refused"] for name, source in pipes.items()}
    check("windows_pipes_judged_by_arguments", all(reason is not None for reason in refused.values()), refused)


def run_all(run, check, plain, holder, autorun_blend, open_with_autorun, driver_file):
    native_stack(run, check)
    addon_after_script(run, check)
    modules_planted(run, check)
    signals_and_settings(run, check)
    carriers_on_entry(run, check, plain, holder, autorun_blend, open_with_autorun, driver_file)
