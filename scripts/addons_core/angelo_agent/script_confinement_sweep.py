# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What an agent script leaves in process-wide places, judged at its end (script_confinement, doc/angelo/
script_confinement.rst, "Work that outlives the script").

A script can put things where nothing raises an event when they are put there, and Angelo or Blender later runs or
reads them with no agent code on the stack. The hook takes a snapshot when a script starts (``begin``) and, when the
executor reports its end, ``end`` acts on what changed:

* **Python process state goes back** (``python_sweep``): the import path and importers (``sys.path``,
  ``sys.meta_path``, ``sys.path_hooks``), the environment (``os.environ``: a later program Angelo starts reads it),
  the builtins, the interpreter's hooks (``sys.excepthook`` and the rest, ``threading.excepthook``), a replaced
  ``sys.modules`` entry, and every name in Angelo's own modules and their classes (a rebound check or path).
* **Blender's callables run confined** (``blender_sweep``): a handler or ``driver_namespace`` function the script
  added runs under the confinement's trampoline, a text it set to run at load (or changed while it runs at load) stops
  running at load, and a widening Angelo setting goes back (``grant_guard.WIDENING``).

Each ``end`` returns short findings for the script's result. Both are built once, at install, from functions bound
then: a script that rebinds this module or a builtin changes nothing they call.
"""


# The standard library modules Angelo's own file, process, network and import code goes through: a function rebound
# in one of them is put back as in Angelo's own modules (a script that rebinds ``shutil.which`` or ``os.lstat`` would
# otherwise steer what Angelo runs or opens later).
STANDARD = ("os", "posix", "nt", "posixpath", "ntpath", "genericpath", "stat", "shutil", "subprocess", "json",
            "json.decoder", "json.encoder", "pathlib", "pathlib._local", "pathlib._abc", "tempfile", "io", "_io",
            "codecs", "socket", "ssl", "urllib.request", "urllib.parse", "http.client", "importlib", "importlib.util",
            "importlib.machinery", "importlib._bootstrap", "importlib._bootstrap_external", "site", "threading",
            "_thread", "functools", "glob", "fnmatch", "sqlite3", "hashlib", "hmac", "secrets", "base64", "uuid",
            "platform", "zipfile", "tarfile", "logging", "atexit", "signal", "selectors", "select", "fcntl", "ctypes",
            "_posixsubprocess", "_winapi", "msvcrt", "webbrowser", "pickle", "copyreg", "marshal", "types", "inspect",
            "traceback", "warnings", "gc")
LARGEST = 4096   # A module-level container with more entries is a cache, not a registry: not compared.


def python_sweep(package, installed_file):
    """(begin, end) for the Python process state; ``package``: Angelo's package name (its modules' functions,
    classes and modules are restored); ``installed_file(name)``: whether a file is installed code."""
    import builtins
    import os
    import sys
    import threading
    _dict, _list, _id, _type, _isinstance, _getattr, _setattr = dict, list, id, type, isinstance, getattr, setattr
    _str, _len, _sorted, _Exception, _callable = str, len, sorted, Exception, callable
    _any, _zip = any, zip
    module_type = _type(sys)
    environ, modules, builtin_names = os.environ, sys.modules, builtins.__dict__
    hooks = ("excepthook", "displayhook", "unraisablehook", "breakpointhook")
    prefix = package + "."

    constant_types = (_str, bytes, int, float, bool, tuple, frozenset, type(None))

    def code_like(value, name=""):
        """What a rebinding replaces and the end puts back: a function, class, module or other callable, or an
        UPPER_CASE constant's immutable value (a path, a flag, a file name). Other data stays as Angelo set it."""
        if _isinstance(value, (_type, module_type)) or _callable(value):
            return True
        return _isinstance(name, _str) and name.isupper() and _isinstance(value, constant_types)

    def installed_entry(entry):
        if _isinstance(entry, _str):
            return installed_file(entry) or not entry
        module = modules.get(_getattr(_type(entry), "__module__", ""))
        return installed_file(_getattr(module, "__file__", None) or "<frozen ") if module is not None else False

    standard = frozenset(STANDARD)

    def kept_callable(value):
        """A callable that may stay in a registry a script changed: installed Python code (Angelo's own, the standard
        library's). A builtin, a partial, a bound builtin, the agent's own function: not."""
        function = _getattr(value, "__func__", value)
        code = _getattr(function, "__code__", None)
        return code is not None and installed_file(_getattr(code, "co_filename", None))

    largest = LARGEST

    def containers_of(values):
        found = []
        for key, value in values.items():
            if _type(value) is _dict and _len(value) <= largest:
                found.append((value, {k: v for k, v in value.items() if _callable(v)}))
            elif _type(value) is _list and _len(value) <= largest:
                found.append((value, _list(value)))
        return found

    def restore_containers(containers, label, findings):
        """Callables a script put into, or swapped in, a module-level registry (a dict or list): swapped entries go
        back, and new ones that are not installed Python code go."""
        changed = 0
        for container, before in containers:
            if _type(container) is _dict:
                for key, value in before.items():
                    if container.get(key, before) is not value:
                        container[key] = value
                        changed += 1
                for key in [key for key, value in _list(container.items())
                            if key not in before and _callable(value) and not kept_callable(value)]:
                    del container[key]
                    changed += 1
            else:
                known = {_id(item) for item in before}
                for item in [item for item in container if _id(item) not in known and _callable(item)
                             and not kept_callable(item)]:
                    container.remove(item)
                    changed += 1
        if changed:
            findings.append(label + ": registry entries (" + _str(changed) + ")")

    def owned(name):
        return name == package or name.startswith(prefix) or name in standard

    stale = _stale_file_finders()

    def begin():
        stale()
        angelo = {}
        for name, module in _list(modules.items()):
            if owned(name) and module is not None:
                values = _dict(_getattr(module, "__dict__", {}))
                values["\0class"] = _type(module)   # A module's class swapped for one that lies on attribute reads.
                classes = {key: _dict(value.__dict__) for key, value in values.items()
                           if _isinstance(value, _type) and _getattr(value, "__module__", None) == name}
                angelo[name] = (module, values, classes, containers_of(values))
        return {
            "path": _list(sys.path), "meta_path": _list(sys.meta_path), "path_hooks": _list(sys.path_hooks),
            "importer_cache": _dict(sys.path_importer_cache), "importer_paths": _finder_paths(),
            "environ": _dict(environ), "builtins": _dict(builtin_names),
            "hooks": {name: _getattr(sys, name, None) for name in hooks},
            "thread_hook": threading.excepthook, "modules": {name: _id(module) for name, module in modules.items()},
            "module_objects": _dict(modules), "angelo": angelo,
        }

    def restore_module(current, before, label, findings):
        """Put back the functions, classes and modules a module had that were rebound, and every name that was
        removed (data that Angelo's own code changed while the script ran stays as it set it)."""
        changed = [key for key in before if key not in current   # A removed name comes back, whatever it held.
                   or (code_like(before[key], key) and current[key] is not before[key])]
        for key in changed:
            current[key] = before[key]
        if changed:
            findings.append(label + ": " + ", ".join(_sorted(_str(key) for key in changed)[:8]))

    # Each stage on its own (a stage that fails is reported and the rest still run), builtins first: every other
    # stage, and the standard library code it calls (``os.environ``'s encoders call ``isinstance``), reads them.
    def restore_builtins(snapshot, findings):
        snapshot["builtins"].pop("_", None)
        missing = owned   # Any object no builtin name holds.
        restore = [key for key in snapshot["builtins"] if builtin_names.get(key, missing) is not
                   snapshot["builtins"][key]]
        added = [key for key in _list(builtin_names) if key != "_" and key not in snapshot["builtins"]]
        for key in restore:
            builtin_names[key] = snapshot["builtins"][key]
        for key in added:
            del builtin_names[key]
        if restore or added:
            findings.append("builtins: " + ", ".join(_sorted(restore + added)[:8]))

    def restore_angelo(snapshot, findings):
        for name, (module, values, classes, containers) in snapshot["angelo"].items():
            try:
                restore_containers(containers, name, findings)
                kind = values.pop("\0class", None)
                if kind is not None and _type(module) is not kind:
                    module.__class__ = kind
                    findings.append(name + ": module class")
                restore_module(module.__dict__, values, name, findings)
                values["\0class"] = kind
                for key, attributes in classes.items():
                    cls = values[key]
                    changed = [attr for attr in attributes if attr not in cls.__dict__
                               or (cls.__dict__[attr] is not attributes[attr] and code_like(attributes[attr], attr))]
                    for attr in changed:
                        try:
                            _setattr(cls, attr, attributes[attr])
                        except (AttributeError, TypeError):
                            pass
                    if changed:
                        findings.append(name + "." + key + ": " + ", ".join(_sorted(changed)[:8]))
            except _Exception:   # noqa: BLE001 - a module that cannot be restored is reported, the rest go on.
                findings.append(name + ": not restored")

    def restore_process(snapshot, findings):
        for name, value in snapshot["hooks"].items():
            if _getattr(sys, name, None) is not value:
                _setattr(sys, name, value)
                findings.append("sys." + name)
        if threading.excepthook is not snapshot["thread_hook"]:
            threading.excepthook = snapshot["thread_hook"]
            findings.append("threading.excepthook")
        for name, ident in snapshot["modules"].items():
            if _id(modules.get(name)) != ident:
                modules[name] = snapshot["module_objects"][name]
                findings.append("sys.modules[" + name + "]")

    def restore_environ(snapshot, findings):
        if _dict(environ) != snapshot["environ"]:
            for key in [key for key in environ if key not in snapshot["environ"]]:
                del environ[key]
            for key, value in snapshot["environ"].items():
                if environ.get(key) != value:
                    environ[key] = value
            findings.append("os.environ")

    def restore_import_path(snapshot, findings):
        # Last: judged with the functions just put back.
        for name in ("path", "meta_path", "path_hooks"):
            current, before = _getattr(sys, name), snapshot[name]
            added = [entry for entry in current if not _any(entry is old for old in before)]
            ordered = _list(before) + [entry for entry in added if installed_entry(entry)]
            if _len(ordered) != _len(current) or _any(a is not b for a, b in _zip(ordered, current)):
                current[:] = ordered   # What the script removed or reordered comes back; only installed code added stays.
                findings.append("sys." + name)
        restore_importer_cache(snapshot["importer_cache"], findings, snapshot["importer_paths"])

    def restore_importer_cache(before, findings, paths=None):
        restored = _importer_cache_restore(sys.path_importer_cache, before, installed_entry, paths)
        if restored:
            findings.append("sys.path_importer_cache (" + _str(restored) + ")")

    stages = (("builtins", restore_builtins), ("modules", restore_angelo), ("process", restore_process),
              ("environment", restore_environ), ("import path", restore_import_path))

    def end(snapshot):
        findings = []
        for label, stage in stages:
            try:
                stage(snapshot, findings)
            except _Exception:   # noqa: BLE001 - reported; the other stages still run.
                findings.append(label + ": not restored")
        stale()
        return findings

    return begin, end


def _finder_paths():
    """Each cached finder's ``path`` (a ``FileFinder`` reads its folder from it): changed in place, it goes back."""
    import sys
    return {key: getattr(finder, "path", None) for key, finder in list(sys.path_importer_cache.items())}


def _stale_file_finders():
    """``mark()``, run at a script's start and end: every cached ``FileFinder`` lists its folder again at its next
    import, as ``importlib.invalidate_caches`` does for them. A finder lists a folder again only when its mtime moves;
    where it does not (seen on Windows), a module made while Angelo runs stayed hidden from the script and from
    Angelo's next import. No finder's method runs (a script may have patched one): only an exact ``FileFinder``'s
    cached mtime is cleared, with functions bound now (test_native_sweep_new_modules)."""
    import sys
    from importlib.machinery import FileFinder
    _type, _list, _values, _set = type, list, dict.values, object.__setattr__

    def mark():
        try:
            for finder in _list(_values(sys.path_importer_cache)):
                if _type(finder) is FileFinder:
                    _set(finder, "_path_mtime", -1)
        except TypeError:   # Not a dict: the import path's restore reports it.
            pass
    return mark


def _importer_cache_restore(cache, before, installed_entry, paths=None):
    """``sys.path_importer_cache`` as the script found it: an entry it replaced goes back, a finder whose ``path`` it
    changed in place points back, and an entry it added stays only when both its folder and its finder are installed
    (a ``FileFinder`` is installed code wherever it points, so its ``path`` is judged too). Dropping an entry only makes
    the import system build it again."""
    restored = 0
    for key, path in (paths or {}).items():
        finder = before.get(key)
        if finder is not None and path is not None and getattr(finder, "path", path) != path:
            try:
                finder.path = path
                restored += 1
            except (AttributeError, TypeError):
                pass
    for key in list(cache):
        finder = cache.get(key)
        if key in before:
            if finder is not before[key]:
                cache[key] = before[key]
                restored += 1
        elif not (installed_entry(key) and installed_entry(finder) and
                  installed_entry(getattr(finder, "path", "") or "")):
            del cache[key]
            restored += 1
    for key, finder in before.items():
        if key not in cache:
            cache[key] = finder
            restored += 1
    return restored


def native_python_sweep(package, installed_file):
    """(begin, end) for the parts of the Python process the C journal does not cover
    (``bpy_angelo_guard_confine_journal.cc`` puts back rebindings in module namespaces, the builtins and ``sys``):
    the import path and importers, the ``os.environ`` mapping, and callables put into module-level lists. (The C
    environment under the mapping, and the rest of the process's state, are put back by C:
    ``bpy_angelo_guard_confine_process.cc``.)
    Cheap: the lists are found once per change in the set of loaded modules."""
    import os
    import sys
    _dict, _list, _id, _type, _len, _any, _zip, _callable = dict, list, id, type, len, any, zip, callable
    _getattr, _isinstance, _str, _sorted = getattr, isinstance, str, sorted
    environ, modules = os.environ, sys.modules
    standard, prefix, largest = frozenset(STANDARD), package + ".", LARGEST
    found = [-1, []]   # (number of loaded modules when found, module-level lists)

    def installed_entry(entry):
        if _isinstance(entry, _str):
            return installed_file(entry) or not entry
        module = modules.get(_getattr(_type(entry), "__module__", ""))
        return installed_file(_getattr(module, "__file__", None) or "<frozen ") if module is not None else False

    def kept_callable(value):
        function = _getattr(value, "__func__", value)
        code = _getattr(function, "__code__", None)
        return code is not None and installed_file(_getattr(code, "co_filename", None))

    def lists():
        if found[0] != _len(modules):
            collected = []
            for name, module in _list(modules.items()):
                if module is not None and (name == package or name.startswith(prefix) or name in standard):
                    for value in _list(_getattr(module, "__dict__", {}).values()):
                        if _type(value) is _list and _len(value) <= largest:
                            collected.append(value)
            found[0], found[1] = _len(modules), collected
        return found[1]

    stale = _stale_file_finders()

    def begin():
        stale()
        return {"path": _list(sys.path), "meta_path": _list(sys.meta_path), "path_hooks": _list(sys.path_hooks),
                "importer_cache": _dict(sys.path_importer_cache), "importer_paths": _finder_paths(),
                "environ": _dict(environ), "lists": [(value, _list(value)) for value in lists()]}

    def end(snapshot):
        findings = []
        try:
            for container, before in snapshot["lists"]:
                known = {_id(item) for item in before}
                planted = [item for item in container if _id(item) not in known and _callable(item)
                           and not kept_callable(item)]
                for item in planted:
                    container.remove(item)
                if planted:
                    findings.append("module-level list entries (" + _str(_len(planted)) + ")")
        except Exception:   # noqa: BLE001 - reported; the rest still run.
            findings.append("lists: not restored")
        try:
            if _dict(environ) != snapshot["environ"]:
                for key in [key for key in environ if key not in snapshot["environ"]]:
                    del environ[key]
                for key, value in snapshot["environ"].items():
                    if environ.get(key) != value:
                        environ[key] = value
                findings.append("os.environ")
        except Exception:   # noqa: BLE001
            findings.append("environment: not restored")
        try:
            for name in ("path", "meta_path", "path_hooks"):
                current, before = _getattr(sys, name), snapshot[name]
                added = [entry for entry in current if not _any(entry is old for old in before)]
                ordered = _list(before) + [entry for entry in added if installed_entry(entry)]
                if _len(ordered) != _len(current) or _any(a is not b for a, b in _zip(ordered, current)):
                    current[:] = ordered
                    findings.append("sys." + name)
            restored = _importer_cache_restore(sys.path_importer_cache, snapshot["importer_cache"], installed_entry,
                                               snapshot["importer_paths"])
            if restored:
                findings.append("sys.path_importer_cache (" + _str(restored) + ")")
        except Exception:   # noqa: BLE001
            findings.append("import path: not restored")
        stale()
        return findings

    return begin, end


def blender_sweep(bpy, defer, is_agent_function, widening=(), widens=None, preferences=None, package=None):
    """(begin, end) for Blender's places: ``bpy.app.handlers`` lists, ``bpy.app.driver_namespace``, texts that run at
    load, and the widening Angelo settings (``preferences()`` gives them). A handler that is a function of Angelo's own
    ``package`` is left as it is (Angelo's code added it)."""
    _list, _dict, _id, _getattr, _setattr, _hash, _callable = list, dict, id, getattr, setattr, hash, callable
    _isinstance, _str, _Exception, _set, _enumerate, list_type = isinstance, str, Exception, set, enumerate, list
    handlers = bpy.app.handlers
    names = [name for name in dir(handlers) if not name.startswith("_")
             and _isinstance(_getattr(handlers, name, None), list_type)]

    def angelo_function(item):
        module = _getattr(item, "__module__", None)
        return (package is not None and _isinstance(module, _str) and (module == package or module.startswith(
            package + ".")) and _getattr(item, "__code__", None) is not None and not is_agent_function(item))

    def texts_now():
        found = {}
        for text in bpy.data.texts:
            if _getattr(text, "use_module", False):
                found[text.name_full] = _hash(text.as_string())
        return found

    def settings_now():
        prefs = preferences() if preferences is not None else None
        return {name: _getattr(prefs, name, None) for name in widening} if prefs is not None else {}

    def begin():
        return {
            "handlers": {name: [_id(item) for item in _getattr(handlers, name)] for name in names},
            "drivers": _dict(bpy.app.driver_namespace), "texts": texts_now(), "settings": settings_now(),
        }

    def end(snapshot):
        findings = []
        for name in names:
            before = _set(snapshot["handlers"].get(name, ()))
            entries = _getattr(handlers, name)
            for index, item in _enumerate(_list(entries)):
                if _id(item) not in before and not is_agent_function(item) and not angelo_function(item):
                    entries[index] = defer(item)
                    findings.append("bpy.app.handlers." + name)
        namespace = bpy.app.driver_namespace
        for key, value in _list(namespace.items()):
            if _callable(value) and (key not in snapshot["drivers"] or snapshot["drivers"][key] is not value) \
                    and not is_agent_function(value):
                namespace[key] = defer(value)
                findings.append("bpy.app.driver_namespace[" + _str(key) + "]")
        for text in bpy.data.texts:
            if _getattr(text, "use_module", False):
                known = snapshot["texts"].get(text.name_full)
                if known is None or known != _hash(text.as_string()):
                    text.use_module = False
                    findings.append("text " + text.name_full + " no longer runs at load")
        prefs = preferences() if preferences is not None else None
        for name, value in snapshot["settings"].items():
            now = _getattr(prefs, name, None)
            if prefs is not None and now != value and (widens is None or widens(now, value)):
                try:
                    _setattr(prefs, name, value)
                    findings.append("setting " + name)
                except _Exception:   # noqa: BLE001 - reported; the grant guard refuses the setting too.
                    findings.append("setting " + name + " not restored")
        return findings

    return begin, end
