# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The destructive steps Angelo's exit and cleanup paths can reach, found by reading the add-on's source
(test_native_destructive_sinks; doc/angelo/script_confinement.rst, "Angelo's own I/O").

**Scope: every destructive call reachable from an exit or cleanup entry point**, not every destructive call in the
package. Entry points are:

* functions registered to run later outside any script: ``atexit.register`` (call or decorator),
  ``threading._register_atexit``, ``weakref.finalize``;
* methods (and module functions) named as cleanup: ``cleanup``, ``close``, ``stop``, ``drain``, ``shutdown``,
  ``release``, ``__del__`` and ``__exit__``.

From them, calls are followed inside the package where their target can be read from the source: a function of the
same module (``name(...)``), a method of the same class (``self.name(...)``), a function or class of another module
of the package imported by name (``module.name(...)``, ``from .module import name``), and a function passed by name
to a registrar or a thread (``atexit.register(name)``, ``Thread(target=name)``). Dynamic dispatch is not followed.

A destructive call is ``shutil.rmtree``, ``os.unlink``/``remove``/``rmdir``/``removedirs``, ``os.replace``/``rename``
(its destination is replaced), a ``.unlink()``/``.rmdir()``/``.replace()``/``.rename()`` method (``Path``), and a call
of the add-on's own replace and remove helpers (``HELPERS``: they act on whatever path their caller gives, so the
caller is the sink; their own bodies are not followed). It goes through the record of what Angelo made when it is a
call of ``owned_paths.remove_tree``/``unlink`` or ``owned_replace.replace``/``remove``, or the function that holds it
asks ``owned_paths.owned`` first. Anything else must be listed in test_native_destructive_sinks' ``ALLOWED`` with its
reason.
"""

import ast
from pathlib import Path

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
CLEANUP_NAMES = frozenset({"cleanup", "close", "stop", "drain", "shutdown", "release", "__del__", "__exit__"})
OS_CALLS = frozenset({"unlink", "remove", "rmdir", "removedirs", "replace", "rename"})
METHOD_CALLS = frozenset({"unlink", "rmdir", "replace", "rename"})
# The add-on's replace and remove helpers: the path they act on is their caller's choice.
HELPERS = {("shared_replace", "replace"), ("shared_replace", "remove"), ("shared_replace", "retried"),
           ("session_journal", "replace"), ("session_journal", "rewrite")}
# Modules whose bodies are the mechanism, not a choice of target: never followed into.
MECHANISMS = frozenset({"shared_replace", "owned_paths", "owned_replace"})


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


class Module:
    """One module's functions (qualified names), imports and destructive calls."""

    def __init__(self, path):
        self.name = path.stem
        self.tree = ast.parse(path.read_text(encoding="utf-8"))
        self.functions = {}   # "f" or "Class.f" -> FunctionDef
        self.imports = {}     # local name -> ("module", None) or ("module", "name")
        for node in self.tree.body:
            self._collect(node, "")
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                for alias in node.names:
                    if node.module:
                        self.imports[alias.asname or alias.name] = (node.module, alias.name)
                    else:
                        self.imports[alias.asname or alias.name] = (alias.name, None)

    def _collect(self, node, prefix):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self.functions[prefix + node.name] = node
            for inner in node.body:   # Nested functions: reachable as their own entries.
                self._collect(inner, prefix + node.name + ".")
        elif isinstance(node, ast.ClassDef):
            for inner in node.body:
                self._collect(inner, prefix + node.name + ".")


def _class_of(qualname):
    return qualname.rsplit(".", 1)[0] if "." in qualname else None


def load(folder=ADDON):
    return {path.stem: Module(path) for path in sorted(Path(folder).glob("*.py"))}


def _resolve(modules, module, qualname, call):
    """The (module, qualname) a call's target names, or None."""
    func = call.func if isinstance(call, ast.Call) else call
    dotted = _dotted(func)
    if dotted is None:
        return None
    parts = dotted.split(".")
    if parts[0] == "self" and len(parts) == 2:
        owner = _class_of(qualname)
        while owner is not None:
            candidate = owner + "." + parts[1]
            if candidate in module.functions:
                return module.name, candidate
            owner = _class_of(owner)
        return None
    if len(parts) == 1:
        if parts[0] in module.functions:
            return module.name, parts[0]
        source = module.imports.get(parts[0])
        if source and source[1] and source[0] in modules and source[1] in modules[source[0]].functions:
            return source[0], source[1]
        return None
    head = module.imports.get(parts[0])
    target = None
    if head and head[1] is None and head[0] in modules:
        target = modules[head[0]]
        rest = parts[1:]
    elif head and head[1] and head[0] in modules:   # from .module import Class -> Class.method
        target, rest = modules[head[0]], [head[1]] + parts[1:]
    elif parts[0] in module.functions or any(name.startswith(parts[0] + ".") for name in module.functions):
        target, rest = module, parts
    if target is None:
        return None
    candidate = ".".join(rest)
    return (target.name, candidate) if candidate in target.functions else None


def entries(modules):
    """(module, qualname) of every exit or cleanup entry point."""
    found = set()
    for module in modules.values():
        for qualname, node in module.functions.items():
            if qualname.rsplit(".", 1)[-1] in CLEANUP_NAMES:
                found.add((module.name, qualname))
            for decorator in node.decorator_list:
                if _dotted(decorator) == "atexit.register":
                    found.add((module.name, qualname))
        for node in ast.walk(module.tree):
            if not isinstance(node, ast.Call):
                continue
            name = _dotted(node.func) or ""
            if name in ("atexit.register", "threading._register_atexit", "weakref.finalize", "register_atexit"):
                arguments = node.args[1:2] if name == "weakref.finalize" else node.args[:1]
                for argument in arguments:
                    target = _resolve(modules, module, _enclosing(module, node), argument)
                    if target:
                        found.add(target)
    return found


def _enclosing(module, target):
    for qualname, node in module.functions.items():
        if any(child is target for child in ast.walk(node)):
            return qualname
    return ""


def _destructive(call):
    dotted = _dotted(call.func) or ""
    if dotted == "shutil.rmtree":
        return dotted
    if dotted.startswith("os.") and dotted[3:] in OS_CALLS:
        return dotted
    if isinstance(call.func, ast.Attribute) and call.func.attr in METHOD_CALLS and not dotted.startswith(
            ("os.", "owned_paths.", "owned_replace.", "str.", "self._lock", "text.", "name.")):
        receiver = dotted.rsplit(".", 1)[0] if dotted else ""
        if call.func.attr == "replace" and (call.args and isinstance(call.args[0], ast.Constant)
                                            and isinstance(call.args[0].value, str)):
            return None   # str.replace("a", "b").
        if call.func.attr == "replace" and call.keywords:
            return None   # datetime.replace(tzinfo=...), dataclasses-style copies.
        return "." + call.func.attr + ("" if receiver else "()")
    return None


def _owned_guarded(node):
    for inner in ast.walk(node):
        if isinstance(inner, ast.Call):
            name = _dotted(inner.func) or ""
            if name in ("owned_paths.owned", "owned"):
                return True
    return False


def reachable(modules):
    """{(module, qualname): set of (module, qualname) it was reached from}."""
    seen, stack = {}, [(entry, entry) for entry in sorted(entries(modules))]
    while stack:
        current, origin = stack.pop()
        if current in seen:
            seen[current].add(origin)
            continue
        seen[current] = {origin}
        module = modules[current[0]]
        node = module.functions[current[1]]
        for inner in ast.walk(node):
            targets = []
            if isinstance(inner, ast.Call):
                targets.append(inner)
                targets += [keyword.value for keyword in inner.keywords if keyword.arg == "target"]
            for target in targets:
                resolved = _resolve(modules, module, current[1], target)
                if resolved and resolved not in seen and resolved[0] not in MECHANISMS and resolved not in HELPERS:
                    stack.append((resolved, origin))
    return seen


def sinks(modules=None):
    """[(module, qualname, line, call, guarded, entries)] of every destructive call reachable from an entry."""
    modules = load() if modules is None else modules
    found = []
    for (name, qualname), origins in sorted(reachable(modules).items()):
        node = modules[name].functions[qualname]
        guarded = _owned_guarded(node)
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call):
                kind = _destructive(inner)
                helper = _resolve(modules, modules[name], qualname, inner)
                if helper in HELPERS:
                    kind = "%s.%s" % helper
                if kind:
                    found.append((name, qualname, inner.lineno, kind, guarded, sorted(origins)))
    return found
