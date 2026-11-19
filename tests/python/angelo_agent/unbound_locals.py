# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Find the names a function reads before it first binds them, when its binding makes them local (UnboundLocalError).

Python decides at compile time that a name bound anywhere in a function (an assignment, an ``import`` or ``from .
import x``, a ``def``, an ``except ... as``, a ``for`` or ``with`` target) is local to all of it. A read earlier in
the function then raises UnboundLocalError instead of reaching the module's name of the same spelling: 2.0.1's
``fab_browser_profile.ProfileLease.cancel_pending`` read ``owned_paths`` (the module's import) a few lines before its
own ``from . import owned_paths``, so every call raised, on every platform, and its tests ran only on macOS.

``findings(source)`` walks each function's own statements in evaluation order (a value before its targets; a nested
function's decorators and defaults in the enclosing one, never its body) and reports a read of a local name that no
binding precedes in source order. ``shadowing`` marks the reads whose name the module (or an enclosing function) also
binds: the code meant that one. A binding in an earlier branch counts as preceding, so a read in a later branch is not
reported (missed rather than wrongly reported); a read that only a loop's earlier pass can precede is reported. No
import of the scanned code: plain ``ast``, so any host scans any file.
"""

import ast
from dataclasses import dataclass

SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


@dataclass(frozen=True)
class Finding:
    function: str
    name: str
    line: int
    shadowing: bool

    def __str__(self):
        kind = "shadows the enclosing name" if self.shadowing else "read before its first binding"
        return "%s: line %d reads %r, which this function binds later (%s)" % (self.function, self.line, self.name,
                                                                              kind)


def _bound_names(node):
    """Names a binding node binds in the scope it runs in."""
    kind = type(node)
    if kind is ast.Name:
        return [] if type(node.ctx) is ast.Load else [node.id]
    if kind is ast.Import or kind is ast.ImportFrom:
        return [(alias.asname or alias.name).split(".")[0] for alias in node.names if alias.name != "*"]
    if kind in DEFINITIONS:
        return [node.name]
    if kind is ast.ExceptHandler or kind is ast.MatchAs or kind is ast.MatchStar:
        return [node.name] if node.name else []
    if kind is ast.MatchMapping:
        return [node.rest] if node.rest else []
    return []


DEFINITIONS = frozenset({ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef})
BINDERS = frozenset({ast.Import, ast.ImportFrom, ast.ExceptHandler, ast.MatchAs, ast.MatchStar, ast.MatchMapping})
COMPREHENSIONS = frozenset({ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp})
LEAVES = frozenset({ast.Name, ast.Constant, ast.Load, ast.Store, ast.Del, ast.Pass, ast.Break, ast.Continue,
                    ast.Global, ast.Nonlocal, ast.Import, ast.ImportFrom})   # Nothing of a scope's code below them.


def _own_nodes(body):
    """Every node of a scope's own code: nested functions' and classes' bodies excluded (their decorators, defaults,
    bases and annotations included), comprehensions' too but their first iterable (run in this scope)."""
    stack = list(body)
    pop, extend, children = stack.pop, stack.extend, ast.iter_child_nodes
    while stack:
        node = pop()
        yield node
        kind = type(node)
        if kind in LEAVES:
            continue
        if kind is ast.FunctionDef or kind is ast.AsyncFunctionDef:
            arguments = node.args
            extend(node.decorator_list + arguments.defaults + [d for d in arguments.kw_defaults if d])
            extend(a.annotation for a in arguments.posonlyargs + arguments.args + arguments.kwonlyargs
                   + [arguments.vararg, arguments.kwarg] if a is not None and a.annotation is not None)
            if node.returns is not None:
                stack.append(node.returns)
        elif kind is ast.Lambda:
            extend(node.args.defaults + [d for d in node.args.kw_defaults if d])
        elif kind is ast.ClassDef:
            extend(node.decorator_list + node.bases + [keyword.value for keyword in node.keywords])
        elif kind in COMPREHENSIONS:
            stack.append(node.generators[0].iter)
        else:
            extend(children(node))


class _Order(ast.NodeVisitor):
    """A scope's own code in evaluation order: each read reported unless a binding of its name came first."""

    def __init__(self, local, bound, outer, label):
        self.local, self.bound, self.outer, self.label, self.found = local, set(bound), outer, label, []

    def bind(self, names):
        self.bound.update(names)

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load) and node.id in self.local and node.id not in self.bound:
            self.found.append(Finding(self.label, node.id, node.lineno, node.id in self.outer))
            self.bound.add(node.id)   # Once per name.
        elif not isinstance(node.ctx, ast.Load):
            self.bind([node.id])

    def visit_Assign(self, node):
        self.visit(node.value)
        for target in node.targets:
            self.visit(target)

    def visit_AnnAssign(self, node):
        if node.value is not None:
            self.visit(node.value)
            self.visit(node.target)

    def visit_AugAssign(self, node):
        self.visit(node.value)
        if isinstance(node.target, ast.Name):   # ``x += 1`` reads x first.
            self.visit_Name(ast.Name(id=node.target.id, ctx=ast.Load(), lineno=node.lineno))
        self.visit(node.target)

    def visit_NamedExpr(self, node):
        self.visit(node.value)
        self.visit(node.target)

    def visit_For(self, node):
        self.visit(node.iter)
        self.visit(node.target)
        for statement in node.body + node.orelse:
            self.visit(statement)

    visit_AsyncFor = visit_For

    def visit_With(self, node):
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self.visit(item.optional_vars)
        for statement in node.body:
            self.visit(statement)

    visit_AsyncWith = visit_With

    def visit_ExceptHandler(self, node):
        if node.type is not None:
            self.visit(node.type)
        self.bind(_bound_names(node))
        for statement in node.body:
            self.visit(statement)

    def visit_Import(self, node):
        self.bind(_bound_names(node))

    visit_ImportFrom = visit_Import

    def _nested(self, node, parts):
        for part in parts:
            self.visit(part)
        self.bind(_bound_names(node))

    def visit_FunctionDef(self, node):
        arguments = node.args
        self._nested(node, node.decorator_list + arguments.defaults + [d for d in arguments.kw_defaults if d])

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        for part in node.args.defaults + [d for d in node.args.kw_defaults if d]:
            self.visit(part)

    def visit_ClassDef(self, node):
        self._nested(node, node.decorator_list + node.bases + [keyword.value for keyword in node.keywords])

    def _comprehension(self, node):
        self.visit(node.generators[0].iter)

    visit_ListComp = visit_SetComp = visit_GeneratorExp = visit_DictComp = _comprehension

    def generic_visit(self, node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.MatchAs, ast.MatchStar, ast.MatchMapping)):
                self.visit(child)
                self.bind(_bound_names(child))
            else:
                self.visit(child)


def _arguments(scope):
    parameters = scope.args
    return {a.arg for a in parameters.posonlyargs + parameters.args + parameters.kwonlyargs
            + [parameters.vararg, parameters.kwarg] if a is not None}


def _survey(body):
    """One pass over a scope's own code: the names it binds (first line each), the names it reads (first line each),
    its ``global``/``nonlocal`` names and the scopes nested in it."""
    bound, read, declared, nested = {}, {}, set(), []
    for node in _own_nodes(body):
        kind = type(node)
        if kind is ast.Name:
            line = node.lineno
            table = read if type(node.ctx) is ast.Load else bound
            if line < table.get(node.id, line + 1):
                table[node.id] = line
            continue
        if kind is ast.Global or kind is ast.Nonlocal:
            declared.update(node.names)
            continue
        if kind in DEFINITIONS or kind is ast.Lambda:
            nested.append(node)
        elif kind not in BINDERS:
            continue
        for name in _bound_names(node):
            line = getattr(node, "lineno", 0)
            if line < bound.get(name, line + 1):
                bound[name] = line
    return bound, read, declared, nested


def findings(source, filename="<source>"):
    """Every ``Finding`` in ``source``: a function's read of a name it binds only later in source order.

    A function is walked in evaluation order (``_Order``) only when a name it binds is read on or before the line of
    its first binding; the first pass over every scope is enough to rule the rest out."""
    tree = ast.parse(source, filename)
    found = []

    def scan(nested, outer, prefix):
        for node in nested:
            if isinstance(node, ast.ClassDef):
                scan(_survey(node.body)[3], outer, prefix + node.name + ".")
                continue
            inner = node.body if isinstance(node.body, list) else [node.body]
            bound, read, declared, below = _survey(inner)
            arguments = _arguments(node)
            local = (set(bound) | arguments) - declared
            label = prefix + getattr(node, "name", "<lambda>")
            if any(read.get(name, line + 1) <= line for name, line in bound.items()
                   if name in local and name not in arguments):
                order = _Order(local, arguments, outer, label)
                for statement in inner:
                    order.visit(statement)
                found.extend(order.found)
            scan(below, outer | local, label + ".")
    bound, _read, _declared, nested = _survey(tree.body)
    scan(nested, set(bound), "")
    return sorted(set(found), key=lambda item: (item.line, item.name))
