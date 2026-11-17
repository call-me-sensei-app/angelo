# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where Angelo's own code writes, replaces or removes files, and where each target path comes from, found by reading
the add-on's source (test_native_write_sinks; doc/angelo/script_confinement.rst, "Angelo's own I/O").

**Scope: every function of the add-on**, whatever runs it: an operator the person runs, a timer, a job's
completion, a thread, an exit or cleanup path.

A sink is a call that writes, replaces, removes, makes or changes what a path names (``write_sink_calls`` lists
them: files written, folders and links made, modes and times changed, archives written or extracted, Blender's writers,
programs started), or a call of an add-on function that passes one of its parameters to a sink (a helper:
``shared_replace.replace``, ``write_json(path, ...)``): the caller's argument is the sink, followed until it is no
parameter.

Each target is classified from the source, within its function:

* ``fixed``: literals, a module's fixed names (``write_sinks_constants``: bound once to an immutable value and never
  rebound; an UPPER_CASE name is no constant by its spelling), a fixed source (``FIXED_SOURCES``: a folder or value C
  or the platform owns; an attribute of a module outside the add-on is fixed only when listed there), a function of
  the add-on whose every return is fixed, and paths built from those;
* ``created``: made by this call chain itself (``tempfile.mkstemp``/``mkdtemp``, ``private_roots.mkdtemp``/
  ``allocate``), or a fresh unique name (``uuid4``, ``token_*``) in a folder that is itself fixed or created: never a
  person's file. A unique name in a ``state`` folder is ``state``;
* ``param``: a parameter (followed to the callers; a function no add-on code calls is an entry: its parameters come
  from Blender, a thread or a timer, so it counts as ``state``);
* ``state``: anything else (an attribute, an object's field, a module's data, a value another call returned).

A ``state`` target must be checked against C's facts in its function (``owned_paths.may_write``/
``require_writable``/``owned`` naming the same value, or the record's own ``owned_paths``/``owned_replace`` calls),
or be listed in test_native_write_sinks' ``ALLOWED`` with why.

Dynamic dispatch onto a sink is not followed, so it is reported (``dynamic:...``): a sink function or method, or an
add-on helper, referenced without a call (stored, passed as a callable, a default value, ``functools.partial``, a
thread's target), ``getattr``/``methodcaller`` naming a sink method, and a ``getattr`` result with a computed name
called. Each is listed in test_native_write_sinks' ``DYNAMIC`` with why, or rewritten as a call the reader follows.
"""

import ast
from pathlib import Path

import destructive_sinks as _d
import write_sinks_constants
from write_sink_calls import (NATIVE_WRITER_TYPES, PROCESS_STARTS, flags_text, native_method_sink,  # noqa: F401
                              os_open_kind, own_nodes, sink_of)
from write_sinks_dispatch import call_views, callable_aliases, dynamic_references, helper_arguments, program_of

ADDON = _d.ADDON
MECHANISMS = frozenset({"owned_paths", "owned_replace"})   # Their bodies check C's record themselves.
# ``mine`` (owned_replace): the file C recorded, or no file yet where C's ``may_write`` says yes.
# owned_paths' checks that end in C's record: create_beside (made by C beside a file C allows), and two that say
# "C allows this path" only in a narrower form (the re-read of 9dcfb883ebb, SHOULD-4):
# - make_dir_beside has C make or adopt a folder below an anchor C accepts: a check only when the name it is given is
#   fixed (a literal or module constant), so no value from state chooses which folder of the person's becomes Angelo's;
# - require_anchorable passes for the open document's folder, which only the anchored descriptors may use: a check
#   only in the module that writes through them (ANCHORED_MODULES).
GUARDS = frozenset({"may_write", "require_writable", "owned", "require_runnable", "may_run", "runnable_argv", "mine",
                    "require_anchorable", "make_dir_beside", "create_beside"})
ANCHORED_MODULES = frozenset({"checkpoint_filesystem"})
CREATING = frozenset({"tempfile.mkstemp", "tempfile.mkdtemp", "tempfile.NamedTemporaryFile",
                      "tempfile.TemporaryDirectory", "private_roots.mkdtemp", "private_roots.allocate",
                      "private_roots.TemporaryDirectory", "mkstemp", "mkdtemp"})
BUILDERS = frozenset({"os.path.join", "Path", "pathlib.Path", "PurePath", "os.fspath", "str", "os.fsdecode",
                      "os.fsencode", "os.path.abspath", "os.path.normpath", "os.path.realpath", "os.path.dirname",
                      "os.path.expanduser", "long_path", "bytes"})
LISTERS = frozenset({"os.listdir", "os.scandir", "os.walk", "glob.glob", "glob.iglob", "sorted", "list", "reversed",
                     "enumerate", "zip", "filter"})   # A folder's entries: as good as the folder.
BUILDER_METHODS = frozenset({"iterdir", "glob", "rglob", "items", "values", "keys", "get", "joinpath", "with_name", "with_suffix", "with_stem", "resolve", "absolute", "expanduser",
                             "format", "parent", "replace", "lower", "strip", "rstrip", "lstrip", "encode", "decode"})
ORDER = {"fixed": 0, "created": 1, "param": 2, "state": 3}
UNIQUE = frozenset({"uuid4", "token_hex", "token_urlsafe", "token_bytes"})   # A name built with one is fresh.


def _join(*classes):
    classes = [c for c in classes if c]
    return max(classes, key=ORDER.get) if classes else "fixed"


class Analysis:
    def __init__(self, folder=ADDON, modules=None):
        self.modules = _d.load(folder) if modules is None else modules
        self.constants = {name: set() for name in self.modules}   # Fixed names, found by run().
        self.absolute = {name: {alias.asname or alias.name.split(".")[0] for node in ast.walk(m.tree)
                                if isinstance(node, ast.Import) for alias in node.names}
                         for name, m in self.modules.items()}
        # ``from os import replace`` -> {"replace": "os.replace"}: a name imported from outside the add-on.
        self.from_absolute = {name: {alias.asname or alias.name: node.module + "." + alias.name
                                     for node in ast.walk(m.tree) if isinstance(node, ast.ImportFrom)
                                     and node.level == 0 and node.module for alias in node.names}
                              for name, m in self.modules.items()}
        self.builder_methods = BUILDER_METHODS
        self.fixed_sources = set()
        self.fixed_functions = set()   # (module, qualname) whose every return is fixed.
        self.helpers = {}              # (module, qualname) -> {parameter name: {kinds}}
        self.followed = {}             # (module, qualname) -> ids of callables its calls follow (write_sinks_dispatch)
        self.sinks = []

    def canonical(self, module, dotted):
        """``storage.data_directory`` for ``data_directory`` imported from ``.storage``; else as written."""
        if not dotted:
            return ""
        head, _, rest = dotted.partition(".")
        source = module.imports.get(head)
        if source is None:
            absolute = self.from_absolute.get(module.name, {}).get(head)
            return absolute + ("." + rest if rest else "") if absolute else dotted
        if source[1] is None:
            return source[0] + ("." + rest if rest else "")
        return source[0] + "." + source[1] + ("." + rest if rest else "")

    # ---- Classification -------------------------------------------------------------------------------------------

    def classify(self, module, qualname, expr, env, params):
        if expr is None:
            return "state"
        if isinstance(expr, ast.Constant):
            return "fixed"
        if isinstance(expr, ast.Name):
            if expr.id in env:
                return env[expr.id]
            if expr.id in params:
                return "param"
            if expr.id in self.absolute[module.name]:
                return "fixed"   # A module imported by name: its functions are classified at the call.
            if expr.id in self.constants[module.name]:
                return "fixed"
            source = module.imports.get(expr.id)   # ``from .storage import ROOT``: that module's fixed name.
            if source is not None and source[1] is not None:
                return "fixed" if source[1] in self.constants.get(source[0], ()) else "state"
            return "fixed" if self._fixed_source(module, expr.id) else "state"
        if isinstance(expr, ast.JoinedStr):
            return _join(*(self.classify(module, qualname, v.value, env, params) for v in expr.values
                           if isinstance(v, ast.FormattedValue)))
        if isinstance(expr, ast.BinOp):
            return _join(self.classify(module, qualname, expr.left, env, params),
                         self.classify(module, qualname, expr.right, env, params))
        if isinstance(expr, (ast.IfExp, ast.BoolOp)):
            parts = [expr.body, expr.orelse] if isinstance(expr, ast.IfExp) else expr.values
            return _join(*(self.classify(module, qualname, p, env, params) for p in parts))
        if isinstance(expr, ast.Subscript):
            return self.classify(module, qualname, expr.value, env, params)
        if isinstance(expr, (ast.Tuple, ast.List)):
            return _join(*(self.classify(module, qualname, e, env, params) for e in expr.elts))
        if isinstance(expr, ast.Starred):
            return self.classify(module, qualname, expr.value, env, params)
        if isinstance(expr, ast.Attribute):
            dotted = _d._dotted(expr) or ""
            if dotted in env:
                return env[dotted]   # ``self.<name>`` an ``__init__`` set (``_constructing``).
            if dotted.startswith(("self.", "cls.")):
                return "state"
            root = expr
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and root.id not in env and root.id not in params:
                source = module.imports.get(root.id)
                if source is not None and root is expr.value:   # An add-on module's name: fixed only if fixed there.
                    return "fixed" if source[1] is None and expr.attr in self.constants.get(source[0], ()) else "state"
                if root.id in self.absolute[module.name] or root.id in self.from_absolute[module.name]:
                    # A module outside the add-on: its data (Blender's, a library's) is state unless listed fixed.
                    return "fixed" if self._fixed_source(module, dotted) else "state"
            return self.classify(module, qualname, expr.value, env, params)   # A part of a path: as the path.
        if isinstance(expr, ast.Call):
            return self._classify_call(module, qualname, expr, env, params)
        return "state"

    def _fixed_source(self, module, dotted):
        return bool(dotted) and (dotted in self.fixed_sources or self.canonical(module, dotted) in self.fixed_sources)

    def fixed_call(self, module, call):
        """Whether a call, given fixed arguments, returns a fixed value: a fixed source, a builder, a fixed
        function of the add-on."""
        dotted = _d._dotted(call.func) or ""
        name = self.canonical(module, dotted)
        resolved = self.resolve(module, "", call)
        return (self._source_call(name, dotted, resolved) or name in BUILDERS or dotted in BUILDERS
                or resolved in self.fixed_functions)

    def _source_call(self, name, dotted, resolved):
        """A call of a fixed source, however it is spelled (``ui.state_directory()``, or ``state_directory()`` in
        ``ui`` itself)."""
        return (name in self.fixed_sources or dotted in self.fixed_sources
                or (resolved is not None and "%s.%s" % resolved in self.fixed_sources))

    def flags_text(self, module, node, params, expr):
        cache = self.__dict__.setdefault("_module_values", {})
        if module.name not in cache:
            cache[module.name] = write_sinks_constants.module_values(module.tree)
        return flags_text(cache[module.name], node, params, expr)

    def _classify_call(self, module, qualname, call, env, params):
        dotted = _d._dotted(call.func) or ""
        name = self.canonical(module, dotted)
        if name in CREATING or dotted in CREATING:
            return "created"
        if dotted.rsplit(".", 1)[-1] in UNIQUE:
            # A fresh unique name: never a file that was there. Only the name: joined with the folder it is made in
            # (``state_folder / uuid4().hex`` is a new file in a folder a script steered: p10c-4's design point b).
            return "created"
        parts = list(call.args) + [k.value for k in call.keywords]   # ``user_resource("CONFIG", path=...)``: all.
        resolved = self.resolve(module, qualname, call)
        if self._source_call(name, dotted, resolved) or resolved in self.fixed_functions:
            return _join(*(self.classify(module, qualname, a, env, params) for a in parts))
        if name in BUILDERS or dotted in BUILDERS or name in LISTERS:
            return _join(*(self.classify(module, qualname, a, env, params) for a in parts))
        if isinstance(call.func, ast.Attribute) and call.func.attr in BUILDER_METHODS:
            return _join(self.classify(module, qualname, call.func.value, env, params),
                         *(self.classify(module, qualname, a, env, params) for a in parts))
        return "state"

    def environment(self, module, qualname, node, params):
        cache = self.__dict__.setdefault("_env_cache", {})
        if id(node) not in cache:
            cache[id(node)] = self._environment(module, qualname, node, params)
        return cache[id(node)]

    def _environment(self, module, qualname, node, params):
        """{local name: class}, flow-insensitive: the worst of every assignment, from an optimistic start to the
        fixed point (so a chain assigned in any order resolves)."""
        assigned = set()
        for inner in own_nodes(node):
            for target in self._targets(inner):
                assigned |= {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}
        constructing = self._constructing(module, qualname, node)
        env = {name: "fixed" for name in assigned | constructing if name not in params}
        for _round in range(8):
            new = {}
            for inner in own_nodes(node):
                pairs = []
                if isinstance(inner, ast.Assign):
                    pairs = [(t, inner.value) for t in inner.targets]
                elif isinstance(inner, (ast.AnnAssign, ast.AugAssign)) and inner.value is not None:
                    pairs = [(inner.target, inner.value)]
                elif isinstance(inner, ast.NamedExpr):
                    pairs = [(inner.target, inner.value)]
                elif isinstance(inner, ast.withitem) and inner.optional_vars is not None:
                    pairs = [(inner.optional_vars, inner.context_expr)]
                elif isinstance(inner, (ast.For, ast.comprehension)):
                    pairs = [(inner.target, inner.iter)]
                for target, value in pairs:
                    cls = self.classify(module, qualname, value, env, params)
                    for name in (n.id for n in ast.walk(target) if isinstance(n, ast.Name)):
                        new[name] = _join(new.get(name), cls)
                    if _d._dotted(target) in constructing:
                        new[_d._dotted(target)] = _join(new.get(_d._dotted(target)), cls)
            for name in params:
                if name in new:
                    new[name] = _join(new[name], "param")
            if new == env:
                break
            env = new
        return env

    def _constructing(self, module, qualname, node):
        """``self.<name>`` an ``__init__`` sets, read in it as the local it is: the object under construction is
        reachable by no one else, unless ``self`` itself is handed out (an argument, a value stored) or another
        function of the class sets the same name (it may run meanwhile)."""
        if not qualname.endswith(".__init__"):
            return set()
        inner_nodes = list(own_nodes(node))
        values = {id(n.value) for n in inner_nodes if isinstance(n, ast.Attribute)}
        if any(isinstance(n, ast.Name) and n.id == "self" and isinstance(n.ctx, ast.Load) and id(n) not in values
               for n in inner_nodes):
            return set()   # ``self`` handed out: someone else may change it.
        own = {_d._dotted(t) for n in inner_nodes for t in self._targets(n) if isinstance(t, ast.Attribute)
               and isinstance(t.value, ast.Name) and t.value.id == "self"}
        owner = qualname.rsplit(".", 1)[0] + "."
        elsewhere = {_d._dotted(t) for name, other in module.functions.items() if name.startswith(owner)
                     and name != qualname for n in own_nodes(other) for t in self._targets(n)
                     if isinstance(t, ast.Attribute)}
        return own - elsewhere

    def resolve(self, module, qualname, call):
        """The add-on function a call runs: ``_d._resolve``'s, or a class's ``__init__`` for ``Class(...)``."""
        found = _d._resolve(self.modules, module, qualname, call)
        if found is None and isinstance(call, ast.Call) and isinstance(call.func, (ast.Name, ast.Attribute)):
            found = _d._resolve(self.modules, module, qualname, ast.Attribute(value=call.func, attr="__init__"))
        return found

    @staticmethod
    def _targets(inner):
        if isinstance(inner, ast.Assign):
            return inner.targets
        if isinstance(inner, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr, ast.For, ast.comprehension)):
            return [inner.target]
        if isinstance(inner, ast.withitem) and inner.optional_vars is not None:
            return [inner.optional_vars]
        return []

    # ---- The package ----------------------------------------------------------------------------------------------

    @staticmethod
    def _params(node):
        args = node.args
        names = [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs]
        names += [a.arg for a in (args.vararg, args.kwarg) if a is not None]
        return [n for n in names if n not in ("self", "cls")]

    def _functions(self):
        for module in self.modules.values():
            for qualname, node in module.functions.items():
                yield module, qualname, node

    def _fixed_returns(self):
        for _round in range(6):
            changed = False
            for module, qualname, node in self._functions():
                key = (module.name, qualname)
                if key in self.fixed_functions:
                    continue
                params = self._params(node)
                returns = [n.value for n in own_nodes(node) if isinstance(n, ast.Return) and n.value is not None]
                if not returns or params:
                    continue
                env = self.environment(module, qualname, node, params)
                if all(self.classify(module, qualname, r, env, params) in ("fixed", "created") for r in returns):
                    self.fixed_functions.add(key)
                    changed = True
            if not changed:
                return

    def _path_set(self, node):
        """Names whose ``filepath``/``filepath_raw`` the function assigns (a Blender object saved to that path)."""
        cache = self.__dict__.setdefault("_path_set_cache", {})
        if id(node) not in cache:
            cache[id(node)] = {t.value.id for n in own_nodes(node) if isinstance(n, ast.Assign) for target in n.targets
                               for t in (target.elts if isinstance(target, ast.Tuple) else [target])
                               if isinstance(t, ast.Attribute) and t.attr in ("filepath", "filepath_raw")
                               and isinstance(t.value, ast.Name)}
        return cache[id(node)]

    def _native_values(self, module, node):
        """{local name: type} of names the function binds to a value of a native writer type (``NATIVE_WRITER_TYPES``):
        made with the type or one of its class methods (``aud.Sound(...)``, ``aud.Sound.file(...)``), or by a method
        of such a value (``sound.resample(...)``)."""
        cache = self.__dict__.setdefault("_native_cache", {})
        if id(node) in cache:
            return cache[id(node)]
        found = {}

        def kind_of(value):
            if not isinstance(value, ast.Call):
                return None
            func = value.func
            name = self.canonical(module, _d._dotted(func) or "")
            for kind in NATIVE_WRITER_TYPES:
                if name == kind or name.startswith(kind + "."):
                    return kind
            if isinstance(func, ast.Attribute):
                inner = func.value
                return found.get(inner.id) if isinstance(inner, ast.Name) else kind_of(inner)
            return None

        assigns = [n for n in own_nodes(node) if isinstance(n, ast.Assign)]
        for _round in range(3):   # A chain assigned in any order.
            for inner in assigns:
                kind = kind_of(inner.value)
                if kind:
                    found.update({t.id: kind for t in inner.targets if isinstance(t, ast.Name)})
        cache[id(node)] = found
        return found

    def _deps(self, node):
        """{local name: every name its assigned values use, transitively} (cached per function)."""
        cache = self.__dict__.setdefault("_deps_cache", {})
        if id(node) in cache:
            return cache[id(node)]
        direct = {}
        for inner in own_nodes(node):
            value = getattr(inner, "value", None) if not isinstance(inner, (ast.For, ast.comprehension)) else inner.iter
            if isinstance(inner, ast.withitem):
                value = inner.context_expr
            for target in self._targets(inner):
                for name in (n.id for n in ast.walk(target) if isinstance(n, ast.Name)):
                    if value is not None:   # Names and dotted attributes (``self.path``) the value is made from.
                        direct.setdefault(name, set()).update(
                            n.id if isinstance(n, ast.Name) else _d._dotted(n) for n in ast.walk(value)
                            if isinstance(n, ast.Name) or (isinstance(n, ast.Attribute) and _d._dotted(n)))
        closed = {}
        for name in direct:
            seen, stack = set(), [name]
            while stack:
                current = stack.pop()
                for used in direct.get(current, ()):
                    if used not in seen:
                        seen.add(used)
                        stack.append(used)
            closed[name] = seen
        cache[id(node)] = closed
        return closed

    def _fixed_last_part(self, module, expr):
        """Whether the path ``expr`` builds ends in a fixed name: a literal or a module constant, last in a ``/``
        join, ``os.path.join`` or ``Path(...)`` (or the whole value)."""
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Div):
            expr = expr.right
        elif isinstance(expr, ast.Call) and expr.args and (_d._dotted(expr.func) or "").rsplit(".", 1)[-1] in {
                "join", "Path", "PurePath"}:
            expr = expr.args[-1]
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            return True
        return isinstance(expr, ast.Name) and expr.id in self.constants[module.name]

    def _guarded(self, module, node, expr, line=None):
        names = {n.id for n in ast.walk(expr) if isinstance(n, ast.Name)} if expr is not None else set()
        deps = self._deps(node)
        names |= {used for name in list(names) for used in deps.get(name, ())}
        dotted_deps = {used for used in names if "." in used}
        attrs = ({_d._dotted(n) for n in ast.walk(expr) if isinstance(n, ast.Attribute)} if expr is not None else set()) | dotted_deps
        text = ast.unparse(expr) if expr is not None else None
        for call in (n for n in own_nodes(node) if isinstance(n, ast.Call)):
            dotted = _d._dotted(call.func) or ""
            guard = dotted.rsplit(".", 1)[-1]
            if guard not in GUARDS:
                continue
            if guard == "require_anchorable" and module.name.rsplit(".", 1)[-1] not in ANCHORED_MODULES:
                continue
            if guard == "make_dir_beside" and not (len(call.args) > 1 and self._fixed_last_part(module, call.args[1])):
                continue
            if line is not None and call.lineno > line:
                continue   # A check after the sink checks nothing it did.
            for arg in call.args[:1]:
                arg_names = {n.id for n in ast.walk(arg) if isinstance(n, ast.Name)} - {"self", "os", "Path"}
                arg_attrs = {_d._dotted(n) for n in ast.walk(arg) if isinstance(n, ast.Attribute)}
                if ast.unparse(arg) == text or arg_names & names or (arg_attrs & attrs) - {None}:
                    return True
        return False

    def run(self, fixed_sources=(), contained=()):
        """``contained``: (module, qualname) of functions whose sinks are confined by construction (a test lists
        why): neither reported nor followed to their callers."""
        self.fixed_sources = set(fixed_sources)
        contained = set(contained)
        for _round in range(8):   # Fixed names and fixed functions build on each other: to the fixed point.
            before = (sum(map(len, self.constants.values())), len(self.fixed_functions))
            write_sinks_constants.constants(self)
            self.__dict__.pop("_env_cache", None)
            self._fixed_returns()
            if (sum(map(len, self.constants.values())), len(self.fixed_functions)) == before:
                break
        self.__dict__.pop("_env_cache", None)
        found = {}
        calls = {}   # (module, qualname) -> [(caller module, caller qualname, call)]
        views = {}   # (module, qualname) -> the calls its code makes (``popen(...)`` with ``popen=subprocess.Popen``)
        for module, qualname, node in self._functions():
            aliases, followed = callable_aliases(node)
            views[(module.name, qualname)] = made = []
            for whole in (n for n in own_nodes(node) if isinstance(n, ast.Call)):
                more, ids = call_views(whole, aliases, lambda d, m=module: self.canonical(m, d))
                made += more
                followed |= ids
            self.followed[(module.name, qualname)] = followed
            for call in made:
                target = self.resolve(module, qualname, call)
                if target is not None:
                    calls.setdefault(target, []).append((module, qualname, call))
        for _round in range(12):
            before = (len(found), sum(len(k) for v in self.helpers.values() for k in v.values()))
            for module, qualname, node in self._functions():
                if module.name in MECHANISMS or (module.name, qualname) in contained:
                    continue
                params = self._params(node)
                env = None
                for call in views[(module.name, qualname)]:
                    dotted = _d._dotted(call.func) or ""
                    entries = []
                    name = self.canonical(module, dotted)   # ``from os import replace``: ``os.replace``.
                    flags = lambda expr, n=node, p=params: self.flags_text(module, n, p, expr)   # noqa: E731
                    sink = sink_of(call, name, flags) or (sink_of(call, dotted, flags) if name != dotted else None)
                    target = self.resolve(module, qualname, call)
                    if sink and target is not None:
                        sink = None   # A function of the add-on (``self.touch()``): its own body is read instead.
                    if (sink is None and isinstance(call.func, ast.Attribute) and call.func.attr == "save"
                            and not call.args and not call.keywords and isinstance(call.func.value, ast.Name)
                            and call.func.value.id in self._path_set(node)):
                        sink = ("save", ast.Attribute(value=call.func.value, attr="filepath_raw"))
                    if (sink is None and target is None and isinstance(call.func, ast.Attribute)
                            and isinstance(call.func.value, ast.Name)):
                        kind = self._native_values(module, node).get(call.func.value.id)
                        sink = native_method_sink(call, kind) if kind else None
                    if sink:
                        entries.append(sink)
                    if target in self.helpers and target[0] not in MECHANISMS:
                        entries += helper_arguments(self, target, call, lambda e, n=node, p=params: self.flags_text(
                            module, n, p, e))
                    kept = {}   # Entries of this call that share a report key: the most reportable one stands.
                    for kind, expr in entries:
                        argv = expr
                        if kind.startswith("run:"):
                            expr = program_of(node, expr)
                        env = self.environment(module, qualname, node, params) if env is None else env
                        cls = self.classify(module, qualname, expr, env, params)
                        key = (module.name, qualname, call.lineno, kind)
                        # ``require_runnable(command[0])`` names the argv list: a check of the program all the same.
                        guarded = self._guarded(module, node, expr, call.lineno) or (
                            argv is not expr and self._guarded(module, node, argv, call.lineno))
                        rank = (not guarded, ORDER[cls])
                        if kind.startswith("os.open?") and cls == "param":
                            pass   # A write only if a caller passes write flags: decided there (helper_arguments).
                        elif key not in kept or rank > kept[key]:   # Never the last written: a set's order.
                            kept[key] = rank
                            found[key] = (cls, guarded, expr)
                        if cls == "param" and not guarded:   # Every entry's parameters, whichever one stands.
                            direct = {n.id for n in ast.walk(expr) if isinstance(n, ast.Name)} if expr is not None else set()
                            deps = self._deps(node)
                            names = (direct | {u for d in direct for u in deps.get(d, ())}) & set(params)
                            for name in names:
                                self.helpers.setdefault((module.name, qualname), {}).setdefault(name, set()).add(kind)
            if (len(found), sum(len(k) for v in self.helpers.values() for k in v.values())) == before:
                break
        self.sinks = []
        for (name, qualname, line, kind), (cls, guarded, expr) in sorted(found.items()):
            if cls == "param" and not calls.get((name, qualname)):
                cls = "state"   # An entry: Blender, a thread or a timer passes the value.
            self.sinks.append((name, qualname, line, kind, cls, guarded))
        self.sinks += self._dynamic(contained)
        return sorted(self.sinks)

    def _dynamic(self, contained):
        """``(module, qualname, line, "dynamic:<what>", "state", False)`` of every dynamic dispatch onto a sink."""
        helpers = {key for key, kinds in self.helpers.items() if kinds}
        out = []
        for module in self.modules.values():
            if module.name in MECHANISMS:
                continue
            scopes = [("", module.tree)] + [(q, n) for q, n in module.functions.items()
                                            if (module.name, q) not in contained]
            for qualname, node in scopes:
                found = dynamic_references(self, module, qualname, node, helpers,
                                           self.followed.get((module.name, qualname), set()))
                out += [(module.name, qualname, line, "dynamic:" + what, "state", False) for line, what in found]
        return out


def sinks(fixed_sources=(), contained=(), modules=None):
    return Analysis(modules=modules).run(fixed_sources, contained)
