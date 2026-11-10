# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The add-on as ``precondition_reach`` walks it: its modules parsed, and what a name, a call or an object resolves to.

``Package`` reads every module once: its functions, classes, relative imports and module instances, and which
exception classes or policy classes carry a closed refusal code. ``resolve_call`` and ``object_class`` say what a
called expression or an object is, as far as a static reading can tell (``local_values``: what a local was assigned).
Standard library only.
"""

import ast
import builtins
from pathlib import Path

# Every builtin exception, with the builtin classes it derives from.
BUILTIN_BASES = {name: {base.__name__ for base in kind.__mro__ if base.__module__ == "builtins"} - {"object"}
                 for name, kind in vars(builtins).items() if isinstance(kind, type) and issubclass(kind, BaseException)}
PLAIN = set(BUILTIN_BASES)
CATCH_ALL = {"Exception", "BaseException"}
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


class Package:
    """The add-on's modules, parsed: their functions, classes and relative imports."""

    def __init__(self, folder, codes):
        self.codes = frozenset(codes)
        self.trees = {path.stem: ast.parse(path.read_text(encoding="utf-8"), str(path))
                      for path in sorted(Path(folder).glob("*.py"))}
        self.functions, self.classes, self.imports = {}, {}, {}
        for module, tree in self.trees.items():
            functions, classes, imports = {}, {}, {}
            for node in tree.body:
                if isinstance(node, FUNCTIONS):
                    functions[node.name] = node
                elif isinstance(node, ast.ClassDef):
                    classes[node.name] = node
                elif (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                      and isinstance(node.value, ast.Attribute) and isinstance(node.value.value, ast.Name)):
                    imports.setdefault(node.targets[0].id, ("attr", node.value.value.id, node.value.attr))
                elif (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                      and isinstance(node.value, ast.Call)):   # A module's instance: ``_store = Store(...)``.
                    imports.setdefault(node.targets[0].id, ("instance", node.value.func))
                elif (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Tuple)
                      and isinstance(node.value, ast.Tuple) and len(node.targets[0].elts) == len(node.value.elts)):
                    for name, value in zip(node.targets[0].elts, node.value.elts):   # ``a, b = mod.a, mod.b``
                        if (isinstance(name, ast.Name) and isinstance(value, ast.Attribute)
                                and isinstance(value.value, ast.Name)):
                            imports.setdefault(name.id, ("attr", value.value.id, value.attr))
            for node in ast.walk(tree):   # Function-level imports too: a module's names, approximately.
                if isinstance(node, ast.ImportFrom) and node.level == 1:
                    for alias in node.names:
                        imports[alias.asname or alias.name] = (("module", alias.name) if node.module is None
                                                               else ("from", node.module, alias.name))
            self.functions[module], self.classes[module], self.imports[module] = functions, classes, imports
        owners = {}
        for module, classes in self.classes.items():
            for cls, node in classes.items():
                for item in node.body:
                    if isinstance(item, FUNCTIONS) and not item.name.startswith("__"):
                        owners.setdefault(item.name, []).append((module, cls + "." + item.name))
        common = set().union(*(dir(kind) for kind in (dict, list, str, set, bytes, tuple, Path, object))) | {
            "new", "remove", "update", "read", "write", "close", "open", "start", "stop", "cancel", "poll", "run",
            "execute", "foreach_get", "foreach_set", "evaluated_get", "as_pointer", "tag", "link", "unlink", "send",
            "request", "result", "wait", "set", "put", "submit", "join", "acquire", "release"}
        self.unique = {name: rows[0] for name, rows in owners.items() if len(rows) == 1 and name not in common}
        self._attributes = {}

    def resolve(self, module, name, depth=0):
        """("func", module, name), ("class", module, name), ("module", name) or None."""
        if depth > 6 or module not in self.trees:
            return None
        if name in self.functions[module]:
            return ("func", module, name)
        if name in self.classes[module]:
            return ("class", module, name)
        found = self.imports[module].get(name)
        if found is None:
            return None
        if found[0] == "module":
            return ("module", found[1]) if found[1] in self.trees else None
        if found[0] == "from":
            return self.resolve(found[1], found[2], depth + 1) if found[1] in self.trees else None
        if found[0] == "instance":
            func, kind = found[1], None
            if isinstance(func, ast.Name):
                kind = self.resolve(module, func.id, depth + 1)
            elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                owner = self.resolve(module, func.value.id, depth + 1)
                kind = self.resolve(owner[1], func.attr, depth + 1) if owner and owner[0] == "module" else None
            return ("instance",) + kind[1:] if kind and kind[0] == "class" else None
        owner = self.resolve(module, found[1], depth + 1)
        return self.resolve(owner[1], found[2], depth + 1) if owner and owner[0] == "module" else None

    def mro(self, module, cls, depth=0):
        out = [(module, cls)]
        for base in self.classes[module][cls].bases if depth < 8 else ():
            found = None
            if isinstance(base, ast.Name):
                found = self.resolve(module, base.id)
            elif isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name):
                owner = self.resolve(module, base.value.id)
                found = self.resolve(owner[1], base.attr) if owner and owner[0] == "module" else None
            if found and found[0] == "class":
                out += self.mro(found[1], found[2], depth + 1)
        return out

    def method(self, module, cls, name):
        for owner, klass in self.mro(module, cls):
            if any(isinstance(item, FUNCTIONS) and item.name == name for item in self.classes[owner][klass].body):
                return owner, klass + "." + name
        return None

    def node(self, key):
        module, qualified = key
        if "." not in qualified:
            return self.functions.get(module, {}).get(qualified)
        cls, name = qualified.split(".")
        return next((item for item in self.classes.get(module, {}).get(cls, ast.ClassDef(body=[])).body
                     if isinstance(item, FUNCTIONS) and item.name == name), None)

    def names(self, module, name):
        """``name`` with every base class name it derives from (add-on classes and the builtins under them)."""
        resolved = self.resolve(module, name)
        out = {name}
        if resolved and resolved[0] == "class":
            for owner, klass in self.mro(resolved[1], resolved[2]):
                out.add(klass)
                out |= {getattr(base, "id", getattr(base, "attr", None)) for base in self.classes[owner][klass].bases}
        for each in list(out):
            out |= BUILTIN_BASES.get(each, set())
        return out | CATCH_ALL

    def class_names(self, module, cls):
        """A class's own name and every base class name (add-on classes and the builtins under them)."""
        out = set()
        for owner, klass in self.mro(module, cls):
            out.add(klass)
            out |= {getattr(base, "id", getattr(base, "attr", None)) for base in self.classes[owner][klass].bases}
        for each in list(out):
            out |= BUILTIN_BASES.get(each, set())
        return out | CATCH_ALL

    def attribute(self, module, cls, name):
        key = (module, cls, name)
        if key not in self._attributes:
            self._attributes[key] = self._attribute(module, cls, name)
        return self._attributes[key]

    def _attribute(self, module, cls, name):
        """The (module, class) a class's methods assign to ``self.<name>``, if one: a constructor
        (``self.store = Store(...)``), or a constructor parameter, taken from the class's construction sites
        (``Fingerprinter(_metadata, _geometry)``, ``_geometry`` a module's instance)."""
        kinds = set()
        for owner, klass in self.mro(module, cls):
            for method in (item for item in self.classes[owner][klass].body if isinstance(item, FUNCTIONS)):
                for node in ast.walk(method):
                    if not (isinstance(node, ast.Assign) and any(
                            isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self"
                            and t.attr == name for t in node.targets)):
                        continue
                    if isinstance(node.value, ast.Call):
                        kind = resolve_call(self, owner, node.value.func)
                        if kind and kind[0] == "class":
                            kinds.add(kind[1:])
                    elif isinstance(node.value, ast.Name) and method.name == "__init__":
                        kinds |= self._passed(owner, klass, [a.arg for a in method.args.args], node.value.id)
        return next(iter(kinds)) if len(kinds) == 1 else None

    def _passed(self, module, cls, parameters, parameter):
        """The (module, class) of what every construction of ``cls`` passes as ``parameter``, as far as it resolves."""
        if parameter not in parameters:
            return set()
        index, kinds = parameters.index(parameter) - 1, set()   # Without self.
        for other, tree in self.trees.items():
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                kind = resolve_call(self, other, node.func)
                if not (kind and kind[0] == "class" and kind[1:] == (module, cls)):
                    continue
                value = node.args[index] if 0 <= index < len(node.args) else next(
                    (k.value for k in node.keywords if k.arg == parameter), None)
                found = object_class(self, other, None, None, value) if value is not None else None
                if found:
                    kinds.add(found)
        return kinds

    def coded_class(self, module, cls):
        """An exception class whose instances carry a closed code without the raise naming one (never UNKNOWN)."""
        for owner, klass in self.mro(module, cls):
            if klass == "RefusalError" or self.policy(owner, klass) is not None:
                return False   # RefusalError's default is UNKNOWN; a policy class's code is its raise's (coded_raise).
            if any(isinstance(item, ast.Constant) and item.value in self.codes - {"UNKNOWN"}
                   for item in ast.walk(self.classes[owner][klass])):
                return True
        return False

    def policy(self, module, cls):
        """A class that maps its first argument through a module dict (``_POLICY.get(code, "UNKNOWN")``): that dict's
        literal entries (key -> code), or None for any other class."""
        for node in ast.walk(self.classes[module][cls]):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
                    and isinstance(node.func.value, ast.Name) and len(node.args) == 2
                    and isinstance(node.args[1], ast.Constant) and node.args[1].value == "UNKNOWN"):
                table = next((item.value for item in self.trees[module].body if isinstance(item, ast.Assign)
                              and any(getattr(t, "id", None) == node.func.value.id for t in item.targets)), None)
                return _policy_entries(table)
        return None

    def coded_raise(self, module, cls, call, function=None):
        """Whether a raise of ``cls`` built by ``call`` (in ``function``) carries a closed code other than UNKNOWN."""
        for owner, klass in self.mro(module, cls):
            entries = self.policy(owner, klass)
            if entries is not None:
                keys = _constants(call.args[0], function) if call.args else []
                return any(k.arg == "refusal_code" and not (isinstance(k.value, ast.Constant) and k.value.value == "UNKNOWN")
                           for k in call.keywords) or bool(keys) and all(
                    entries.get(key, "UNKNOWN") != "UNKNOWN" for key in keys)
        return self.coded_class(module, cls)


def _constants(node, function):
    """The constant values ``node`` can be: a constant, a conditional of constants, or a local name assigned only
    those; [] when it can be anything else."""
    if isinstance(node, ast.Constant):
        return [node.value]
    if isinstance(node, ast.IfExp):
        body, orelse = _constants(node.body, function), _constants(node.orelse, function)
        return body + orelse if body and orelse else []
    if isinstance(node, ast.Name) and function is not None:
        assigned = [item.value for item in ast.walk(function) if isinstance(item, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == node.id for t in item.targets)]
        values = [_constants(value, None) for value in assigned]
        return [v for row in values for v in row] if assigned and all(values) else []
    return []


def _policy_entries(table):
    """A literal dict of domain codes to refusal codes, ``**dict.fromkeys((...), CODE)`` entries included."""
    entries = {}
    if not isinstance(table, ast.Dict):
        return entries
    for key, value in zip(table.keys, table.values):
        if key is None and isinstance(value, ast.Call) and getattr(value.func, "attr", None) == "fromkeys" \
                and len(value.args) == 2 and isinstance(value.args[1], ast.Constant):
            names = value.args[0].elts if isinstance(value.args[0], (ast.Tuple, ast.List)) else []
            entries.update({n.value: value.args[1].value for n in names if isinstance(n, ast.Constant)})
        elif isinstance(key, ast.Constant) and isinstance(value, ast.Constant):
            entries[key.value] = value.value
    return entries


def resolve_call(package, module, func):
    """What a called expression names: ("func" | "class", module, name), or None (``f``, ``module.f``)."""
    if isinstance(func, ast.Name):
        return package.resolve(module, func.id)
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        owner = package.resolve(module, func.value.id)
        if owner and owner[0] == "module":
            return package.resolve(owner[1], func.attr)
    return None


def local_values(function, name):
    """The values ``function`` assigns to the local ``name``."""
    return [node.value for node in ast.walk(function) if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)]


def object_class(package, module, cls, function, value):
    """The (module, class) an expression's object is, when the walk can tell: ``self``, a module's instance, a class,
    an object built in place (``Cls(...).method()``), a local assigned one, or a ``self`` attribute assigned one."""
    if isinstance(value, ast.Name):
        if value.id in ("self", "cls") and cls:
            return module, cls
        owner = package.resolve(module, value.id)
        if owner and owner[0] in ("class", "instance"):
            return owner[1], owner[2]
        built = [resolve_call(package, module, v.func) for v in (local_values(function, value.id) if function else ())
                 if isinstance(v, ast.Call)]
        kinds = {kind[1:] for kind in built if kind and kind[0] == "class"}
        return next(iter(kinds)) if len(kinds) == 1 else None
    if isinstance(value, ast.Call):
        kind = resolve_call(package, module, value.func)
        return kind[1:] if kind and kind[0] == "class" else None
    if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name) and value.value.id == "self" and cls:
        return package.attribute(module, cls, value.attr)
    return None
