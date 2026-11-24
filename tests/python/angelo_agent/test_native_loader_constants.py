# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a library load or a program start is made from is never a mutable module-level container.

A confined script may change a module's dict or list in place without any audit event, and the end-of-script sweep
restores only immutable UPPER_CASE values (``script_confinement_sweep``). A container that feeds ``ctypes.CDLL`` or a
program start would then let a script choose what Angelo's own code loads or runs later, unconfined. Break-it review
of the Linux runtime, SHOULD-3: ``desktop_linux.LIBRARIES`` and ``secret_service_linux.LIBRARIES`` were dicts.

The scan follows each loader or spawner call's arguments back through the function: names assigned from others,
and the iterables of loops and comprehensions whose targets reach the call. Any UPPER_CASE module name it reaches
that is bound to anything but an immutable value (``immutable``) is a failure. No Blender.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
LOADERS = frozenset({"CDLL", "PyDLL", "LoadLibrary", "WinDLL", "dlopen"})
SPAWNERS = frozenset({"Popen", "run", "call", "check_call", "check_output", "execv", "execve", "execvp", "execvpe",
                      "execl", "execlp", "posix_spawn", "posix_spawnp", "spawnv", "spawnve", "startfile", "popen"})
FROZEN_BUILDERS = frozenset({"frozenset", "tuple", "MappingProxyType", "str", "bytes", "int", "float", "bool"})
TYPE_BUILDERS = frozenset({"POINTER", "CFUNCTYPE", "WINFUNCTYPE", "PYFUNCTYPE"})   # ctypes: they return a type.
COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _callee(node):
    return node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else None


def _is_loader_or_spawner(node):
    """``ctypes.CDLL``, ``CDLL``, ``subprocess.run``, ``os.execv``, ``getattr(ctypes, "CDLL")``...: the thing itself,
    not a call of it."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr" \
            and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
        return node.args[1].value in LOADERS or node.args[1].value in SPAWNERS
    if isinstance(node, ast.Name):
        return node.id in LOADERS
    if isinstance(node, ast.Attribute):
        owner = node.value
        return node.attr in LOADERS or (node.attr in SPAWNERS and isinstance(owner, ast.Name)
                                        and owner.id in ("subprocess", "os", "child_process"))
    return False


def _aliases(function):
    """Names in ``function`` bound to a loader or spawner under another name: ``load = ctypes.CDLL if load is None
    else load``, ``run = run or subprocess.run``, or a parameter defaulting to one (re-review of d67b4e25575,
    SHOULD-5: an aliased loader hid secret_service_linux's mutable table)."""
    names = set()
    arguments = function.args
    for argument, default in zip(arguments.args[len(arguments.args) - len(arguments.defaults):], arguments.defaults):
        if any(_is_loader_or_spawner(node) for node in ast.walk(default)):
            names.add(argument.arg)
    for argument, default in zip(arguments.kwonlyargs, arguments.kw_defaults):
        if default is not None and any(_is_loader_or_spawner(node) for node in ast.walk(default)):
            names.add(argument.arg)
    for node in ast.walk(function):
        if isinstance(node, ast.Assign) and any(_is_loader_or_spawner(part) for part in ast.walk(node.value)):
            names |= {target.id for target in node.targets if isinstance(target, ast.Name)}
    return names


def _reaches_a_load_or_start(call, aliases=frozenset()):
    return _is_loader_or_spawner(call.func) or (isinstance(call.func, ast.Name) and call.func.id in aliases)


def _names(node):
    return {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}


def fed_names(function):
    """Every name whose value can reach a load or program start's arguments inside ``function``."""
    aliases = _aliases(function)
    calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)
             and _reaches_a_load_or_start(node, aliases)]
    reached = set()
    for call in calls:
        for argument in [*call.args, *(keyword.value for keyword in call.keywords)]:
            reached |= _names(argument)
    while True:
        grown = set(reached)
        for node in ast.walk(function):
            if isinstance(node, ast.Assign) and any(_names(target) & reached for target in node.targets):
                grown |= _names(node.value)
            elif isinstance(node, (ast.For, ast.AsyncFor)) and _names(node.target) & reached:
                grown |= _names(node.iter)
            elif isinstance(node, COMPREHENSIONS):
                for generator in node.generators:
                    if _names(generator.target) & reached or calls and any(
                            call in ast.walk(node) for call in calls):
                        grown |= _names(generator.iter)
        if grown == reached:
            return reached
        reached = grown


def immutable(node, mutable=frozenset()):
    """``node`` builds a value no script can change in place: constants, tuples, ``frozenset``/``MappingProxyType``
    of all-immutable displays, operators over those, a reference to a type or module attribute (``ctypes.c_int``, a
    ctypes ``POINTER(...)``), and names not bound to a mutable value (re-review of 1c31d63dead, NIT-5:
    ``dict({...})``, ``LIBS: dict = {...}`` and ``(["lib"],)`` read as immutable before). Any other call's result
    counts as mutable."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Name):
        return node.id not in mutable
    if isinstance(node, ast.Attribute):
        return immutable(node.value, mutable)
    if isinstance(node, ast.Call) and _callee(node.func) in TYPE_BUILDERS and not node.keywords:
        return all(immutable(argument, mutable) for argument in node.args)
    if isinstance(node, ast.Tuple):
        return all(immutable(item, mutable) for item in node.elts)
    if isinstance(node, (ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.IfExp, ast.JoinedStr, ast.FormattedValue)):
        return all(immutable(child, mutable) for child in ast.iter_child_nodes(node)
                   if isinstance(child, ast.expr))
    if isinstance(node, ast.Call) and _callee(node.func) in FROZEN_BUILDERS and not node.keywords:
        return all(_frozen_contents(argument, mutable) for argument in node.args)
    return False


def _frozen_contents(node, mutable):
    """An argument a frozen builder copies: an immutable value, or a list, set or dict display of immutable items."""
    if immutable(node, mutable):
        return True
    if isinstance(node, (ast.List, ast.Set)):
        return all(immutable(item, mutable) for item in node.elts)
    if isinstance(node, ast.Dict):
        return all(key is not None and immutable(key, mutable) and immutable(value, mutable)
                   for key, value in zip(node.keys, node.values))
    return False


def module_constants(tree):
    """Module names, split into those bound only to immutable values and the rest (a name bound twice is mutable
    when either value is)."""
    bindings = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            bindings += [(target.id, node.value) for target in node.targets if isinstance(target, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and node.value is not None and isinstance(node.target, ast.Name):
            bindings.append((node.target.id, node.value))
    frozen, mutable = set(), set()
    for name, value in bindings:
        if immutable(value, mutable) and name not in mutable:
            frozen.add(name)
        else:
            frozen.discard(name)
            mutable.add(name)
    return frozen, mutable


def violations(source, label="module"):
    tree = ast.parse(source)
    _frozen, mutable = module_constants(tree)
    mutable = {name for name in mutable if name.isupper()}
    found = []
    for function in ast.walk(tree):
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found += ["%s.%s: %s" % (label, function.name, name) for name in sorted(fed_names(function) & mutable)]
    return found


class LoaderConstantTests(unittest.TestCase):
    def test_the_scan_finds_a_mutable_library_table(self):
        source = ('import ctypes\nLIBS = {"gio": "libgio-2.0.so.0"}\nCMD = ["tool"]\nOK = ("libc.so.6",)\n'
                  'def load():\n    return {k: ctypes.CDLL(v) for k, v in LIBS.items()}\n'
                  'def run(subprocess):\n    args = CMD + ["-x"]\n    subprocess.run(args)\n'
                  'def fine():\n    return ctypes.CDLL(OK[0])\n')
        self.assertEqual(violations(source), ["module.load: LIBS", "module.run: CMD"])

    def test_the_scan_follows_a_loader_or_spawner_under_another_name(self):
        source = ('import ctypes, subprocess\nLIBS = {"gio": "libgio-2.0.so.0"}\nCMD = ["tool"]\n'
                  'def load(load=None):\n    load = ctypes.CDLL if load is None else load\n'
                  '    return {k: load(v) for k, v in LIBS.items()}\n'
                  'def run(run=None):\n    run = run or subprocess.run\n    run(CMD)\n'
                  'def started(spawn=subprocess.Popen):\n    spawn(CMD)\n')
        self.assertEqual(violations(source), ["module.load: LIBS", "module.run: CMD", "module.started: CMD"])

    def test_the_scan_reads_any_mutable_value_and_getattr_loaders(self):
        """Re-review of 1c31d63dead, NIT-5: each rewrite passed the display-only scan."""
        cases = {"dict call": 'LIBS = dict(g="libgio-2.0.so.0")\n',
                 "annotated": 'LIBS: dict = {"g": "libgio-2.0.so.0"}\n',
                 "tuple of a list": 'LIBS = (["libgio-2.0.so.0"],)\n',
                 "proxy of a list": 'import types\nLIBS = types.MappingProxyType({"g": ["libgio-2.0.so.0"]})\n',
                 "a call's result": 'LIBS = make()\n'}
        for label, binding in cases.items():
            with self.subTest(label):
                source = "import ctypes\n" + binding + "def load():\n    return ctypes.CDLL(LIBS[0])\n"
                self.assertEqual(violations(source), ["module.load: LIBS"])
        source = 'import ctypes\nLIBS = ["libgio-2.0.so.0"]\ndef load():\n    return getattr(ctypes, "CDLL")(LIBS[0])\n'
        self.assertEqual(violations(source), ["module.load: LIBS"])
        fine = ('import ctypes, types\nA = ("libc.so.6",)\nB = types.MappingProxyType({"g": ("x", 1)})\n'
                'C = frozenset({"a", "b"})\nD = A + ("libm.so.6",)\nE: str = "libz.so.1"\n'
                'def load():\n    return [ctypes.CDLL(x) for x in (*A, *B, *C, *D, E)]\n')
        self.assertEqual(violations(fine), [])

    def test_no_addon_load_or_start_is_fed_by_a_mutable_constant(self):
        found = []
        for path in sorted(ADDON.rglob("*.py")):
            found += violations(path.read_text(encoding="utf-8"), str(path.relative_to(ADDON)))
        self.assertEqual(found, [], "make these immutable (a tuple of pairs, a frozenset): a confined script can "
                                    "change a module's dict or list in place and the sweep does not restore it")


if __name__ == "__main__":
    unittest.main()
