# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which module-level names of the add-on hold a fixed value, for ``write_sinks`` (test_native_write_sinks;
doc/angelo/script_confinement.rst, "Angelo's own I/O").

A name is fixed only when all of these hold, whatever its spelling (an UPPER_CASE name is no constant by its name):

* it is bound exactly once in its module's own scope (an assignment of that one name), and nothing else binds it:
  no second assignment, augmented assignment, loop, ``with``, import, definition, ``del`` or ``global`` in a
  function, and no ``module.NAME = ...``, ``setattr(module, ...)`` or ``globals()[...] = ...`` anywhere in the add-on;
* its value is immutable: a literal str, bytes or number (a bool or ``None`` too), a tuple or ``frozenset`` of
  those, an expression of fixed values (``"a" + NAME``, an f-string of them), or a path derived from a fixed source
  (``FIXED_SOURCES``: a folder C or the platform owns), a fixed name or a function of the add-on whose every return
  is fixed, with the path builders (``Path(...) / "x"``, ``os.path.join``).

A list, dict, set or any other mutable value is state: a script that reaches it changes it in place, and a region's
journal puts back only what was rebound, never what was changed in place.
"""

import ast

import destructive_sinks as _d

_IMMUTABLE_CALLS = frozenset({"frozenset", "tuple"})


def _names(target):
    return [n.id for n in ast.walk(target) if isinstance(n, ast.Name)]


def module_scope(tree):
    """Every node of a module's own scope: never a function's, a class's, a lambda's or a comprehension's."""
    stack = list(ast.iter_child_nodes(tree))
    while stack:
        inner = stack.pop()
        yield inner
        if not isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda, ast.ListComp,
                                  ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            stack.extend(ast.iter_child_nodes(inner))


def bindings(tree):
    """``({name: times bound in the module's scope}, {name: its value when bound by ``name = value``})``."""
    counts, values = {}, {}

    def bind(names, value=None, times=1):
        for name in names:
            counts[name] = counts.get(name, 0) + times
            if value is not None:
                values[name] = value
    for node in module_scope(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                bind(_names(target), node.value if isinstance(target, ast.Name) else None)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            bind(_names(node.target), node.value if isinstance(node.target, ast.Name) else None)
        elif isinstance(node, (ast.AugAssign, ast.Delete)):
            for target in (node.targets if isinstance(node, ast.Delete) else [node.target]):
                bind(_names(target), times=2)   # Changed after it was bound: never fixed.
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.NamedExpr)):
            bind(_names(node.target), times=2)
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            bind(_names(node.optional_vars), times=2)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            bind([(alias.asname or alias.name).split(".")[0] for alias in node.names])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bind([node.name])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bind([node.name], times=2)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            bind([node.name], times=2)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            bind([node.rest], times=2)
    return counts, values


def module_values(tree):
    """{name: every value its module's own scope assigns it} (``BINARY = os.O_BINARY`` and, after an ``except``,
    ``BINARY = 0``)."""
    found = {}
    for node in module_scope(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found.setdefault(target.id, []).append(node.value)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and node.value is not None and isinstance(
                node.target, ast.Name):
            found.setdefault(node.target.id, []).append(node.value)
    return found


def rebound(modules):
    """{module: names something in the add-on rebinds after its scope bound them; ``"*"`` for any name}."""
    found = {name: set() for name in modules}
    for module in modules.values():
        own = found[module.name]
        for node in ast.walk(module.tree):
            if isinstance(node, (ast.Global, ast.Nonlocal)):
                own.update(node.names)
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                targets = [node.target]
            elif isinstance(node, ast.Delete):
                targets = node.targets
            for target in (t for target in targets for t in ast.walk(target)):
                if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                    owner = _module_named(modules, module, target.value.id)
                    if owner is not None:
                        found[owner].add(target.attr)
                elif (isinstance(target, ast.Subscript) and isinstance(target.value, ast.Call)
                      and _d._dotted(target.value.func) in ("globals", "vars")):
                    own.add("*")
            if isinstance(node, ast.Call) and _d._dotted(node.func) in ("setattr", "delattr") and node.args:
                owner = _module_named(modules, module, _d._dotted(node.args[0]) or "")
                if owner is not None:
                    name = node.args[1] if len(node.args) > 1 else None
                    found[owner].add(name.value if isinstance(name, ast.Constant) else "*")
    return found


def _module_named(modules, module, name):
    """The add-on module a local name is bound to (``from . import storage``, ``import ...storage``), or None."""
    source = module.imports.get(name)
    if source is not None and source[1] is None and source[0] in modules:
        return source[0]
    return None


def fixed_value(analysis, module, expr):
    """Whether ``expr``, a module-level value, is immutable and fixed (the module docstring's second rule)."""
    if isinstance(expr, ast.Constant):
        return True
    if isinstance(expr, ast.Tuple):
        return all(fixed_value(analysis, module, e) for e in expr.elts)
    if isinstance(expr, ast.JoinedStr):
        return all(fixed_value(analysis, module, v.value) for v in expr.values if isinstance(v, ast.FormattedValue))
    if isinstance(expr, ast.BinOp):
        return fixed_value(analysis, module, expr.left) and fixed_value(analysis, module, expr.right)
    if isinstance(expr, ast.UnaryOp):
        return fixed_value(analysis, module, expr.operand)
    if isinstance(expr, (ast.IfExp, ast.BoolOp)):
        parts = [expr.body, expr.orelse] if isinstance(expr, ast.IfExp) else expr.values
        return all(fixed_value(analysis, module, p) for p in parts)
    if isinstance(expr, ast.Subscript):
        return fixed_value(analysis, module, expr.value) and fixed_value(analysis, module, expr.slice)
    if isinstance(expr, (ast.Name, ast.Attribute)):
        return analysis.classify(module, "", expr, {}, []) == "fixed"
    if not isinstance(expr, ast.Call) or any(isinstance(a, ast.Starred) for a in expr.args):
        return False
    parts = list(expr.args) + [k.value for k in expr.keywords]
    dotted = _d._dotted(expr.func) or ""
    if dotted in _IMMUTABLE_CALLS:   # ``frozenset({"a", "b"})``: the display is consumed, never kept.
        return all(all(fixed_value(analysis, module, e) for e in p.elts) if isinstance(p, (ast.Set, ast.List))
                   else fixed_value(analysis, module, p) for p in parts)
    if not all(fixed_value(analysis, module, p) for p in parts):
        return False
    if isinstance(expr.func, ast.Attribute) and expr.func.attr in analysis.builder_methods:
        return fixed_value(analysis, module, expr.func.value)   # ``Path(ROOT).joinpath("x")``.
    return analysis.fixed_call(module, expr)


def constants(analysis):
    """{module: its fixed names}, to the fixed point (a fixed name may be built from another)."""
    changed = {name: rebound_names for name, rebound_names in rebound(analysis.modules).items()}
    scopes = {name: bindings(m.tree) for name, m in analysis.modules.items()}
    found = {name: set() for name in analysis.modules}
    for _round in range(16):
        grew = False
        for name, module in analysis.modules.items():
            if "*" in changed[name]:
                continue
            counts, values = scopes[name]
            for constant, value in values.items():
                if (constant in found[name] or counts.get(constant) != 1 or constant in changed[name]
                        or not fixed_value(analysis, module, value)):
                    continue
                found[name].add(constant)
                grew = True
        analysis.constants = found
        if not grew:
            break
    return found
