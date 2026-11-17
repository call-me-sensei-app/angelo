# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Nothing a script leaves behind runs outside its confinement, in the real app (doc/angelo/script_confinement.rst;
break-it review 2026-11-15, MUST-1..5 and SHOULD-1/2). Angelo is enabled as it starts (``install_app``: the real
private folders, the wrappers, the journal, the sweeps); each script runs through ``run_confined`` and is read back
as the executor reads it.

* The escape matrix (``escapes_table``): every trigger that runs code later × every carrier it can call. After
  all scripts, Angelo's side does what it may do later: collect garbage, show and hash objects it holds, call what
  its classes, modules, registries and objects hold. No payload may run unconfined or read the key.
* Class attributes and a module definition made under an installed file's name do not outlast the script.
* SQLite reads or writes no private file and loads no native code; a text set to run when a file opens is not
  saved that way; descriptors Angelo holds are not read or written through.
* Ordinary code still runs: namedtuple defaults, asyncio, unittest.mock, sqlite on a project file, operator and
  property registration.

Run only in a disposable instance: ``--background --factory-startup --disable-autoexec``. No provider or paid call.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import gc
import importlib
import inspect
import json
import shutil
import socket
import sqlite3
import tempfile
import traceback
import types
from pathlib import Path

import addon_utils
import bpy

import escapes_table as table
import native_inventory as inventory_table

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-confinement-escapes-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("ANGELO_STATE_DIR", str(OUT / "state"))
os.environ.setdefault("ANGELO_CONNECTIONS_DIR", str(OUT / "connections"))
os.environ["ANGELO_DISABLE_KEYCHAIN"] = "1"
REPORT = {"success": False, "checks": {}}
PLAIN = OUT / "plain"
FLAGS = OUT / "escape-flags.txt"
HOLDER = types.SimpleNamespace()   # An object Angelo holds, made before any script.


def check(name, ok, detail=None):
    REPORT["checks"][name] = bool(ok)
    if not ok and detail is not None:
        REPORT.setdefault("details", {})[name] = str(detail)[:600]


def runner(names):
    from angelo_agent import script_confinement, script_errors
    from confinement_attacks_admit import admit
    finishing = "finish" in inspect.signature(script_confinement.run_confined).parameters

    def run(source):
        namespace = {"__name__": "__angelo_script__", "bpy": bpy, **names}
        admit("escape", source)
        outcome = {"refused": None}
        try:
            if finishing:   # The executor's read-back, inside the confinement.
                _, result = script_confinement.run_confined(
                    source, namespace, None, "escape", finish=lambda ns: script_errors.read_back(ns.get("result")))
            else:   # As the executor read it back before: after the script, then the namespace is freed.
                script_confinement.run_confined(source, namespace, None, "escape")
                result = script_errors.read_back(namespace.get("result"))
                namespace.clear()
                gc.collect()
            outcome["result"] = json.dumps(result, default=str)[:200]
        except script_confinement.ScriptConfined as error:
            outcome["refused"] = script_confinement.reason_of(error)
        except Exception as error:   # noqa: BLE001 - reported as the check's detail.
            outcome["error"] = repr(error)[:300]
        return outcome
    return run


def later():
    """What Angelo's own code may do once the scripts have ended."""
    from angelo_agent import grant_guard, private_conventions
    from angelo_agent.files import FileWorkspace
    actions = {
        "collect": gc.collect,
        "str_held": lambda: str(getattr(HOLDER, "shown", "")),
        "hash_held": lambda: hash(getattr(HOLDER, "hashed", 0)),
        "finalize_held": lambda: getattr(HOLDER, "finalizer", lambda: None)(),
        "drop_target2": lambda: (HOLDER.__dict__.pop("target2", None), gc.collect()),
        "class_attribute": lambda: getattr(FileWorkspace, "breakit_planted", lambda: None)(),
        "class_method": lambda: json.dumps(object(), cls=json.JSONEncoder),
        "module_name": lambda: getattr(private_conventions, "breakit_planted", lambda: None)(),
        "module_registry": lambda: grant_guard.ENTRIES.get("breakit.planted", lambda: None)(),
        "held_attribute": lambda: getattr(HOLDER, "cb", lambda: None)(),
    }
    for name, action in actions.items():
        try:
            action()
        except Exception:   # noqa: BLE001 - a refusal or the payload's own failure: the flags say what ran.
            pass
    gc.collect()


def escapes(run, key):
    outcomes = {}
    for trigger, (part, carriers, _) in table.TRIGGERS.items():
        for carrier in carriers or table.CARRIERS:
            tag = trigger + "/" + carrier
            payload = table.PAYLOAD.format(tag=tag, key=str(key), flags=str(FLAGS))
            outcomes[tag] = run("P = %r\n" % payload + table.CARRIERS[carrier] + part)
    later()
    lines = FLAGS.read_text().splitlines() if FLAGS.exists() else []
    escaped = [line for line in lines if "confined=False" in line or "read=True" in line]
    check("no_escape", not escaped, escaped)
    check("payloads_ran_confined", any("confined=True" in line for line in lines), lines[:10])
    REPORT["escape_outcomes"] = {tag: outcome for tag, outcome in outcomes.items() if outcome.get("error")}


def rebindings(run, before):
    import json as _json
    from angelo_agent import controller
    stop, encoder, which, copyfile = before
    outcomes = {name: run(source) for name, source in table.REBINDINGS.items()}
    check("class_method_put_back", controller.Controller.__dict__["stop"] is stop, outcomes["class_plain"])
    check("class_names_removed", not any(name in controller.Controller.__dict__ for name in ("breakit",)) and not any(
        name.startswith("breakit") for name in _json.JSONEncoder.__dict__), sorted(_json.JSONEncoder.__dict__))
    check("class_dict_unchanged", dict(_json.JSONEncoder.__dict__) == encoder)
    check("blender_class_method_removed", "breakit_method" not in bpy.types.Operator.__dict__)
    check("definitions_put_back", shutil.which is which and shutil.copyfile is copyfile,
          {name: outcomes[name] for name in ("definition_installed_name", "definition_frozen_name")})
    # What survived is put back here, so a failing build still runs the checks after this one.
    shutil.which, shutil.copyfile = which, copyfile
    type.__setattr__(controller.Controller, "stop", stop)
    for name in [name for name in controller.Controller.__dict__ if name.startswith("breakit")]:
        type.__delattr__(controller.Controller, name)
    for name in [name for name in _json.JSONEncoder.__dict__ if name not in encoder]:
        type.__delattr__(_json.JSONEncoder, name)
    bpy.types.Operator.__dict__.get("breakit_method") and delattr(bpy.types.Operator, "breakit_method")


def shipped_extension():
    """A native extension module file this Python ships, wherever it keeps them (lib-dynload on Linux and macOS,
    DLLs on Windows, where some modules such as _bisect are built in and have no file): copied as the script's own
    "written" library."""
    import importlib.machinery
    suffixes = tuple(importlib.machinery.EXTENSION_SUFFIXES)
    for folder in (Path(sys.base_prefix) / "DLLs", *Path(sys.base_prefix).glob("lib/python3*/lib-dynload")):
        if folder.is_dir():
            found = sorted(path for path in folder.iterdir() if path.name.endswith(suffixes))
            if found:
                return found[0]
    raise AssertionError("no extension module file under %s" % sys.base_prefix)


def sqlite_checks(run, state):
    private_db = state / "breakit.db"
    with sqlite3.connect(private_db) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS t (secret)")
        connection.execute("INSERT INTO t VALUES ('DBSECRET')")
    rows = []
    extension = PLAIN / "written-by-script.so"
    shutil.copyfile(shipped_extension(), extension)
    names = {"PRIVATE_DB": str(private_db), "STATE": str(state), "PLAIN": str(PLAIN), "ROWS": rows,
             "EXTENSION": str(extension)}
    for name, source in table.SQLITE.items():
        outcome = run(source, **names)
        check(name + "_stopped", outcome["refused"] is not None or "error" in outcome, outcome)
    check("sqlite_read_nothing", rows == [], rows)
    check("sqlite_wrote_nothing", not (state / "planted.db").exists() and not (state / "vacuumed.db").exists())


def bundled_modules():
    """Every extension module the app ships (its Python's lib-dynload and site-packages, Blender's own folders) by
    top-level name, and every module built into it."""
    import importlib.machinery
    suffixes = tuple(importlib.machinery.EXTENSION_SUFFIXES)
    names = set(sys.builtin_module_names)
    for base in {path for path in sys.path if path and os.path.isdir(path)}:
        for entry in os.listdir(base):
            full = os.path.join(base, entry)
            if entry.endswith(suffixes):
                names.add(entry.split(".")[0])
            elif os.path.isdir(full) and os.path.isfile(os.path.join(full, "__init__.py")):
                for folder, _, files in os.walk(full):
                    if any(name.endswith(suffixes) for name in files):
                        names.add(entry)
                        break
    return names


def inventory(run):
    import importlib.machinery
    import _bpy
    suffixes = tuple(importlib.machinery.EXTENSION_SUFFIXES)
    refused = set(_bpy._angelo_guard.native_file_modules())
    classified = (inventory_table.REFUSED | set(inventory_table.GATED) | inventory_table.PYTHON_IO |
                  inventory_table.NO_FILES)
    found = bundled_modules()
    check("every_bundled_module_classified", not (found - classified), sorted(found - classified))
    check("refused_list_matches_c", inventory_table.REFUSED <= refused, sorted(inventory_table.REFUSED - refused))
    for name in sorted((found & inventory_table.REFUSED) - set(sys.modules)):
        outcome = run("import %s\n" % name)
        check("import_refused_" + name, outcome["refused"] is not None, outcome)
        # importlib.import_module raises no `import` event: an extension is refused where it is made. A package's
        # pure-Python top may load, but none of its native modules does.
        outcome = run("import importlib\nimportlib.import_module(%r)\n" % name)
        native = sorted(module for module, value in list(sys.modules.items())
                        if (module == name or module.startswith(name + ".")) and
                        str(getattr(value, "__file__", "") or "").endswith(suffixes))
        check("import_module_refused_" + name, outcome["refused"] is not None or not native, (outcome, native))


def main():
    from angelo_agent import ui
    state = Path(ui.state_directory())
    key = state / "keys" / "provider.key"
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text("sk-original")
    PLAIN.mkdir(parents=True, exist_ok=True)
    FLAGS.unlink(missing_ok=True)
    from angelo_agent import controller
    before = (controller.Controller.__dict__["stop"], dict(json.JSONEncoder.__dict__), shutil.which,
              shutil.copyfile)   # As installed, before any script.
    base = runner({"HOLDER": HOLDER})
    escapes(base, key)
    rebindings(base, before)

    def run(source, **names):
        return runner({"HOLDER": HOLDER, **names})(source)
    sqlite_checks(run, state)

    autorun = PLAIN / "autorun-source.blend"   # Made before the script, with a text that runs when it opens.
    text = bpy.data.texts.new("autorun.py")
    text.write("import os")
    text.use_module = True
    bpy.data.libraries.write(str(autorun), {text}, compress=False)
    bpy.data.texts.remove(text)
    for name, source in table.AUTORUN.items():
        REPORT.setdefault("autorun", {})[name] = run(source, PLAIN=str(PLAIN), AUTORUN_BLEND=str(autorun))
    check("use_module_not_set", not any(t.use_module for t in bpy.data.texts if t.name.startswith("breakit_hook")))
    # The wrappers keep Python's "supports" sets true: `os.open in os.supports_dir_fd` is checked by identity
    # (pipeline-gates: checkpoint capture refused "descriptor-relative file tools unavailable").
    missing = [(name, member.__name__) for name in ("supports_dir_fd", "supports_fd", "supports_follow_symlinks",
                                                    "supports_effective_ids")
               for member in getattr(os, name, ()) if getattr(os, member.__name__, member) not in getattr(os, name)]
    try:
        from angelo_agent import checkpoint_filesystem
        anchored = checkpoint_filesystem.anchored_files_supported()
    except ImportError:
        anchored = True
    # shutil decides at import whether rmtree is safe from symlink races (`_use_fd_functions`, an identity check):
    # a copy imported now, after the wrappers, and one a confined script imports, decide as the startup one did.
    import importlib.util
    import shutil as started_shutil

    def fresh_shutil():
        spec = importlib.util.spec_from_file_location("angelo_shutil_probe", started_shutil.__file__)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module._use_fd_functions
    seen = {}
    confined = run("import importlib.util\nspec = importlib.util.spec_from_file_location('angelo_shutil_probe2', "
                   "PATH)\nm = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(m)\n"
                   "SEEN['fd'] = m._use_fd_functions\n"
                   "import sys\ncheckpoint_filesystem = sys.modules['angelo_agent.checkpoint_filesystem']\n"
                   "SEEN['anchored'] = checkpoint_filesystem.anchored_files_supported()\n",
                   PATH=started_shutil.__file__, SEEN=seen)
    platform = started_shutil._use_fd_functions
    check("os_supports_sets_follow_wrappers",
          missing == [] and (anchored or os.name == "nt") and fresh_shutil() == platform and
          seen.get("fd") == platform and (seen.get("anchored") or os.name == "nt"),
          (missing, anchored, platform, fresh_shutil(), seen, confined))
    import escapes_after   # Windows' own native entry points (T0 at 758b07e8b77, build3).
    escapes_after.windows_entries(run, check, key)
    import escapes_later   # Devin break-it 2026-11-15: G1, G4, G5, G6.
    escapes_later.run_all(run, check, PLAIN, HOLDER, autorun)
    for saved in ("project.blend", "appended.blend"):
        path = PLAIN / saved
        if path.exists():
            with bpy.data.libraries.load(str(path)) as (source, _):
                names = list(source.texts)
            with bpy.data.libraries.load(str(path), link=False) as (source, target):
                target.texts = names
            check("saved_without_autorun_" + saved, not any(t.use_module for t in target.texts if t is not None),
                  names)

    held_key = os.open(str(key), os.O_RDONLY)
    left, right = socket.socketpair()
    rows = []
    try:
        for name, source in table.DESCRIPTORS.items():
            if name == "fd_pread_private" and not hasattr(os, "pread"):
                continue   # POSIX only (Windows has no os.pread).
            outcome = run(source, HELD_KEY=held_key, HELD_SOCKET=left.fileno(), ROWS=rows)
            # On Windows a socket is a handle, not a C descriptor: os.write can't reach it at all (EBADF), and
            # descriptors_leaked_nothing below confirms nothing arrived.
            unreachable = sys.platform == "win32" and name == "fd_write_socket" and \
                "Bad file descriptor" in outcome.get("error", "")
            check(name + "_refused", outcome["refused"] is not None or unreachable, outcome)
        right.setblocking(False)
        try:
            received = right.recv(100)
        except BlockingIOError:
            received = b""
        check("descriptors_leaked_nothing", rows == [] and received == b"", (rows, received))
    finally:
        os.close(held_key)
        left.close()
        right.close()

    for name, source in table.ORDINARY.items():
        outcome = run(source, PLAIN=str(PLAIN))
        check("ordinary_" + name, outcome["refused"] is None and "error" not in outcome, outcome)

    installed_imports_unchanged(run)
    installed_package_definitions(run)
    cache_matrix(run)
    inventory(run)
    from angelo_agent.script_confinement_blender import os_startup
    for index, target in enumerate(os_startup()[:3] + os_startup()[-1:]):   # A shell file, and the user site.
        outcome = run("import os\nos.makedirs(os.path.dirname(TARGET), exist_ok=True)\n"
                      "open(TARGET if not os.path.isdir(TARGET) else TARGET + '/x.pth', 'a').write('import os')\n",
                      TARGET=target)
        check("os_startup_refused_%d" % index, outcome["refused"] is not None, (target, outcome))


def _shape(module):
    """What an installed module defines: each name's type, and each of its own classes' attributes' types."""
    shape = {}
    for key, value in vars(module).items():
        shape[key] = type(value).__name__
        if isinstance(value, type) and value.__module__ == module.__name__:
            for attribute, held in vars(value).items():
                shape[key + "." + attribute] = type(held).__name__
    return shape


def installed_imports_unchanged(run):
    """An installed module a confined script imports first defines itself, and stays as it defined itself after the
    region (pipeline-gates, 2026-11-15): what its own top level binds, what installed helpers it calls copy into it
    (``enum.global_enum``, a package helper like USD's ``Tf.PrepareModule``), what a multi-phase extension's C code
    adds in ``_imp.exec_dynamic``/``exec_builtin``, and its classes' wrapped methods (an ``lru_cache``, a
    ``cached_property``) keep their type. Checked on every unloaded candidate found: extensions, built-ins and
    pure-Python modules."""
    # First the modules that exercise each way a module defines itself: names an installed helper copies in
    # (``enum.global_enum``, ``IntEnum._convert_``) and classes with ``lru_cache``/``cached_property`` methods.
    helper_copies = ("calendar", "ssl", "socket", "signal")
    wrapped_methods = ("ipaddress", "platform", "configparser", "importlib.metadata")
    candidates = list(helper_copies + wrapped_methods) + sorted(set(sys.builtin_module_names) | {
        "_bisect", "_heapq", "_csv", "_statistics", "_zoneinfo", "_lsprof", "cmath", "_contextvars", "_queue",
        "_random", "array", "_json", "_struct", "_datetime", "unicodedata", "zoneinfo", "plistlib", "tomllib",
        "statistics", "fractions", "pstats", "http.cookies", "uuid", "graphlib", "email.headerregistry",
        "optparse", "pickletools"})
    checked = {"extension": 0, "python": 0}
    covered = {"helper_copies": [], "wrapped_methods": []}
    restore = {}
    if all(name in sys.modules for name in helper_copies):
        # All already loaded by the app: import socket afresh (``IntEnum._convert_`` puts its enum classes, which
        # are callables, into it from enum's frame), and put the app's own module back afterwards.
        restore["socket"] = sys.modules.pop("socket")
    try:
        _check_imports(run, candidates, helper_copies, checked, covered)
    finally:
        sys.modules.update(restore)
    check("installed_imports_checked", checked["extension"] > 0 and checked["python"] > 0, checked)
    for way, names in covered.items():
        check("installed_imports_cover_" + way, names != [], covered)


def _check_imports(run, candidates, helper_copies, checked, covered):
    import importlib.util
    for name in candidates:
        if name in sys.modules:
            continue
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ValueError):
            continue
        if spec is None:
            continue
        kind = "python" if (spec.origin or "").endswith(".py") else "extension"
        if checked[kind] >= 8:
            continue
        seen = {}
        outcome = run("import importlib, json\nSEEN.update(json.loads(SHAPE(importlib.import_module(NAME))))\n",
                      NAME=name, SEEN=seen, SHAPE=lambda module: json.dumps(_shape(module)))
        if outcome["refused"] is not None:
            continue   # A module that opens files natively: refused, so nothing defined.
        module = sys.modules.get(name)
        after = _shape(module) if module is not None else {}
        changed = {key: (seen.get(key), after.get(key)) for key in set(seen) | set(after)
                   if seen.get(key) != after.get(key)}
        check("installed_import_unchanged_" + name, seen != {} and changed == {}, (outcome, changed))
        checked[kind] += 1
        if name in helper_copies:
            covered["helper_copies"].append(name)
        if {"_lru_cache_wrapper", "cached_property"} & set(seen.values()):
            covered["wrapped_methods"].append(name)


def installed_package_definitions(run):
    """Installed packages that define themselves as USD's and typing's do (``table.IMPORT_FIXTURES``), imported
    first by a confined script: a helper's copies (``__spec__`` included) and later names stay, their classes keep
    ``lru_cache``/``cached_property`` methods and ``Protocol`` attributes; a script-written file one of them loads is
    the script's code wherever it lands (pipeline-gates, 2026-11-15: pxr.UsdUtils, opentimelineio)."""
    import _bpy
    root = Path(bpy.utils.user_resource("SCRIPTS", path="modules", create=True))
    for relative, source in table.IMPORT_FIXTURES.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(source)
    plugin = PLAIN / "angelo_fixture_plugin_file.py"
    (root / "angelo_fixture_plugins" / "plugin_path.txt").write_text(str(plugin))
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    seen = {}
    outcome = run("import importlib, json\nm = importlib.import_module('angelo_fixture_defines')\n"
                  "SEEN.update(json.loads(SHAPE(m)))\n", SEEN=seen, SHAPE=lambda module: json.dumps(_shape(module)))
    module = sys.modules.get("angelo_fixture_defines")
    after = _shape(module) if module is not None else {}
    changed = {key: (seen.get(key), after.get(key)) for key in set(seen) | set(after)
               if seen.get(key) != after.get(key)}
    check("package_definitions_kept", outcome["refused"] is None and seen != {} and changed == {}, (outcome, changed))
    try:
        works = (module.made(), module.late(), module.Cached().twice(2), module.Cached().lazy, module.native_sqrt(4))
        module.Cached.twice.cache_clear()
    except Exception as error:   # noqa: BLE001 - the detail says what broke.
        works = repr(error)
    check("package_definitions_work", works == (1, 3, 4, 1, 2.0), works)
    # The same package loaded by a loader called directly (`module_from_spec` + `exec_module`, as a plugin system or
    # a gate does), not through the import system's own frame.
    direct = root / "angelo_fixture_direct"
    direct.mkdir(parents=True, exist_ok=True)
    for name in ("__init__.py", "_source.py"):
        (direct / name).write_text(table.IMPORT_FIXTURES["angelo_fixture_defines/" + name])
    seen = {}
    outcome = run("import importlib.util, json, sys\nspec = importlib.util.find_spec('angelo_fixture_direct')\n"
                  "m = importlib.util.module_from_spec(spec)\nsys.modules[spec.name] = m\n"
                  "spec.loader.exec_module(m)\nSEEN.update(json.loads(SHAPE(m)))\n", SEEN=seen,
                  SHAPE=lambda module: json.dumps(_shape(module)))
    # The script's own module object never outlives it (escapes_loading: what it chose before the body ran would
    # run as Angelo's); Angelo's next import of the name loads the package whole, as the script saw it.
    left = sys.modules.get("angelo_fixture_direct")
    module = importlib.import_module("angelo_fixture_direct")
    after = _shape(module) if module is not None else {}
    changed = {key: (seen.get(key), after.get(key)) for key in set(seen) | set(after)
               if seen.get(key) != after.get(key)}
    check("package_loaded_directly_kept", outcome["refused"] is None and seen != {} and changed == {} and
          left is None, (outcome, changed, left is None))
    outcome = run("open(PLUGIN, 'w').write(SOURCE)\nimport angelo_fixture_plugins\n", PLUGIN=str(plugin),
                  SOURCE=table.PLUGIN_FILE)
    plugins = sys.modules.get("angelo_fixture_plugins")
    handler = getattr(plugins, "handler", None)
    held = plugins.Holder.__dict__.get("held") if plugins is not None else None
    check("package_loaded_script_code_confined", outcome["refused"] is not None or (
        (handler is None or _bpy._angelo_guard.is_deferred(handler)) and
        (held is None or type(held).__name__ in ("ConfinedAttr", "ConfinedDataAttr"))),
        (outcome, type(handler).__name__, type(held).__name__))


def cache_matrix(run):
    """Every standard-library cache and memo pattern a script fills keeps working, for later scripts and for Angelo
    (``table.CACHE_MATRIX``): what installed code made on a script's behalf and keeps in its own state is installed
    code's, so it is never left confined (pipeline-gates: ``importlib.metadata``'s cached bound methods)."""
    confining = ("Deferred", "ConfinedAttr", "ConfinedDataAttr")
    held = []
    for name, source in table.CACHE_MATRIX.items():
        outcomes = [run(source, HELD=held) for _ in range(2)]
        try:
            exec(source, {"__name__": "angelo_matrix", "HELD": held})   # noqa: S102 - Angelo's side, unconfined.
            plain = None
        except Exception as error:   # noqa: BLE001 - the detail says what broke.
            plain = repr(error)
        check("cache_" + name, all(o["refused"] is None and "error" not in o for o in outcomes) and plain is None,
              (outcomes, plain))
    left = []
    for module_name in table.CACHE_MODULES:
        module = sys.modules.get(module_name)
        for key, value in (vars(module).items() if module is not None else ()):
            if type(value).__name__ in confining:
                left.append(module_name + "." + key)
            if isinstance(value, type) and value.__module__ == module_name:
                left += [module_name + "." + key + "." + attribute for attribute, inner in vars(value).items()
                         if type(inner).__name__ in confining]
    left += ["FastPath(%s).%s" % (vars(path).get("root"), key) for path in gc.get_objects()
             if type(path).__name__ == "FastPath" for key, inner in vars(path).items()
             if type(inner).__name__ in confining]
    check("cache_modules_hold_no_confining_wrappers", left == [], left)


try:
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    main()
    REPORT["success"] = bool(REPORT["checks"]) and all(REPORT["checks"].values())
except Exception:   # noqa: BLE001 - the receipt says why.
    REPORT["error"] = traceback.format_exc()
(OUT / "confinement-escapes.json").write_text(json.dumps(REPORT, indent=2, default=str))
print(json.dumps({key: value for key, value in REPORT.items() if key != "escape_outcomes"}, default=str)[:6000])
if REPORT["success"]:
    print("ANGELO_CONFINEMENT_ESCAPES_OK")
sys.exit(0 if REPORT["success"] else 1)
