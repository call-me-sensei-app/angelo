# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whether a script may run code that isn't its own text (2.1.0 break-it review A-2; round 2 N-3; round 3 S-1).

Always run this exact script (off in 2.1.0, ``reviewed_scripts.OFFERED``; this check then only decides whether to
show its button) covers a script's own bytes. Code it reaches beyond them (a text block, a file, a module
by name, a string, another program) can change while those bytes stay the same, so a script that may reach any is not
offered it (``reviewed_scripts.runs_other_code``). Judged on the syntax tree:

* **One judgement per name,** whatever form the script gives it: a name, an attribute, a constant argument to
  ``getattr``/``setattr``/``attrgetter``/``methodcaller``, or an imported name. ``RUNS`` are names that run, load or
  build code, or reach the interpreter's own machinery. ``compile`` counts as Python's own, never ``re.compile``;
  ``system`` and ``__dict__`` count only on a module that runs programs (``os.system``, ``shutil.os.system``), never
  ``platform.system()`` or a scene's unit system.
* **Imports from an allow-list:** the app's own modules and those of Python's that neither load nor run other code
  (``ALLOWED``). Anything else (a module beside the script, ``pickle``, ``subprocess``, ``pdb``'s string runners), a
  relative import and a star import withhold it.
* **The import system's state** (``sys.modules``, ``sys.path``), a module that runs programs passed or assigned on
  as a value, a computed lookup, a driver expression that isn't a constant, Blender's driver namespace and a file
  opened with its scripts on withhold it too. Text that doesn't parse can't be judged and withholds it.

A check over text can't recognise every way to reach code, and the card says so (``chat_cards.REMEMBER_LIMIT``).
Each script is judged once, by its digest: a card asks several times a frame. Standard library only; no bpy.
"""

import ast
import functools
import hashlib
import threading
from collections import OrderedDict

ALLOWED = frozenset({
    # The app's own.
    "bpy", "bmesh", "mathutils", "gpu", "gpu_extras", "bl_math", "bpy_extras", "idprop", "aud", "freestyle", "imbuf",
    "bpy_types", "rna_prop_ui", "nodeitems_utils", "numpy",
    # Python's, that neither load nor run other code.
    "__future__", "abc", "array", "ast", "base64", "binascii", "bisect", "bz2", "calendar", "cmath", "collections",
    "colorsys", "concurrent", "contextlib", "copy", "csv", "dataclasses", "datetime", "decimal", "difflib", "enum",
    "errno", "fnmatch", "fractions", "functools", "gc", "glob", "gzip", "hashlib", "heapq", "hmac", "html", "http",
    "io", "itertools", "json", "keyword", "locale", "logging", "lzma", "math", "mimetypes", "numbers", "operator", "os",
    "pathlib", "platform", "pprint", "queue", "random", "re", "reprlib", "secrets", "shlex", "shutil", "sqlite3",
    "statistics", "string", "struct", "sys", "tarfile", "tempfile", "textwrap", "threading", "time", "traceback",
    "types", "typing", "unicodedata", "urllib", "uuid", "warnings", "weakref", "xml", "zipfile", "zlib", "zoneinfo"})
RUNS = frozenset({
    "exec", "eval", "execfile", "__import__", "breakpoint", "globals", "locals",
    "as_module", "run_script", "python_file_run", "load_scripts", "modules_from_path", "addon_enable", "addon_install",
    "addon_refresh", "use_module", "import_module", "load_module", "exec_module", "run_path", "run_module",
    "execute_preset", "driver_namespace",
    "FunctionType", "CodeType", "LambdaType", "__code__", "__globals__", "__builtins__", "__subclasses__", "__base__",
    "__bases__", "__mro__", "__getattribute__", "__loader__", "__spec__", "f_globals", "f_locals", "f_builtins",
    "popen", "startfile", "fork", "forkpty", "posix_spawn", "posix_spawnp", "create_subprocess_exec",
    "create_subprocess_shell", "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe",
    "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe"})
BARE = frozenset({"compile"})                      # Python's own as a bare name; re.compile is something else.
PROGRAM_MODULES = frozenset({"os", "posix", "nt"})
ON_PROGRAM_MODULES = frozenset({"system", "__dict__"})
IMPORT_STATE = frozenset({"modules", "path", "meta_path", "path_hooks", "path_importer_cache"})   # On sys.
_LOOKUPS = {"getattr": 1, "setattr": 1, "attrgetter": 0, "methodcaller": 0}   # The argument naming what is reached.
_MEMO, _MEMO_SIZE, _lock = OrderedDict(), 128, threading.Lock()


def _chain(node):
    """The names along an expression's object chain: ``shutil.os.system`` -> system, os, shutil."""
    names = []
    while True:
        if isinstance(node, ast.Attribute):
            names.append(node.attr)
            node = node.value
        elif isinstance(node, (ast.Subscript, ast.Call)):
            node = node.value if isinstance(node, ast.Subscript) else node.func
        elif isinstance(node, ast.Name):
            names.append(node.id)
            return names
        else:
            return names


class _Judge:
    def __init__(self, tree):
        self.modules = {}   # Name -> the module an import binds to it.
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    bound = alias.asname or alias.name.split(".")[0]
                    self.modules[bound] = alias.name if alias.asname else alias.name.split(".")[0]

    def _is(self, names, wanted):
        """Whether one of ``names`` is one of the ``wanted`` modules, by its own name or the name it was imported as."""
        return any(name in wanted or self.modules.get(name) in wanted for name in names)

    def attribute(self, name, base):
        """Whether reaching ``name`` on ``base`` (an expression) runs code."""
        if name in RUNS:
            return True
        chain = _chain(base) if base is not None else []
        if name in ON_PROGRAM_MODULES and self._is(chain, PROGRAM_MODULES):
            return True
        return name in IMPORT_STATE and self._is(chain[-1:], {"sys"})

    def imported(self, node):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level or not module or any(alias.name == "*" for alias in node.names):
                return True
            root = module.split(".")[0]
            reached = any(alias.name in RUNS or alias.name in BARE and root == "builtins"
                          or root in PROGRAM_MODULES and alias.name in ON_PROGRAM_MODULES
                          or root == "sys" and alias.name in IMPORT_STATE for alias in node.names)
            return reached or root not in ALLOWED
        return any(alias.name.split(".")[0] not in ALLOWED for alias in node.names)

    def call(self, node):
        func = node.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
        index = _LOOKUPS.get(name)
        if index is not None:
            if len(node.args) <= index or not isinstance(node.args[index], ast.Constant) \
                    or not isinstance(node.args[index].value, str):
                return True   # A name computed while it runs could be any of them.
            if self.attribute(node.args[index].value, node.args[0] if index else None):
                return True
        return any(keyword.arg in ("use_scripts", "allow_pickle") and not (
            isinstance(keyword.value, ast.Constant) and not keyword.value.value) for keyword in node.keywords)

    def escapes(self, tree):
        """A module that runs programs (or ``sys``) used as a value rather than reached into: passed, assigned on."""
        reached = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        risky = PROGRAM_MODULES | {"sys"}
        return any(isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and id(node) not in reached
                   and self.modules.get(node.id) in risky
                   for node in ast.walk(tree))


def _judge(code):
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return True
    judge = _Judge(tree)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)) and judge.imported(node):
            return True
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and (node.id in RUNS or node.id in BARE):
            return True
        if isinstance(node, ast.Attribute) and judge.attribute(node.attr, node.value):
            return True
        if isinstance(node, ast.Call) and judge.call(node):
            return True
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Attribute) and target.attr == "expression" for target in targets) and not (
                    isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
                return True   # A driver's expression is Python Blender runs; a constant one is the script's own.
    return judge.escapes(tree)


@functools.lru_cache(maxsize=16)
def text_key(code):
    """``(SHA-256 hex of code, whether it is strict UTF-8)``, kept for the few scripts pending at once: the pump and
    each card draw ask at every tick (Fable review 1, A-5: 0.96 ms per hash of a 120 KB script on Mac 1)."""
    try:
        return hashlib.sha256(code.encode("utf-8")).hexdigest(), True
    except UnicodeEncodeError:   # A lone surrogate: still judged, by the bytes it passes through.
        return hashlib.sha256(code.encode("utf-8", "surrogatepass")).hexdigest(), False


def reaches(code):
    """Whether ``code`` may run code that isn't its own text (module docstring); kept by the text's digest."""
    key = text_key(code)[0]
    with _lock:
        if key in _MEMO:
            _MEMO.move_to_end(key)
            return _MEMO[key]
    found = _judge(code)
    with _lock:
        _MEMO[key] = found
        while len(_MEMO) > _MEMO_SIZE:
            _MEMO.popitem(last=False)
    return found
