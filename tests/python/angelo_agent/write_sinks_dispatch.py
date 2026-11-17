# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Calls ``write_sinks`` reaches through a callable, and the dispatch it can't follow (test_native_write_sinks;
doc/angelo/script_confinement.rst, "Angelo's own I/O").

Followed, so the sink behind them is checked like a direct call:

* a call of a parameter whose default is a callable (``def run(..., popen=subprocess.Popen)``: ``popen(command)``
  is ``subprocess.Popen(command)``), of a local assigned one (``run = run or subprocess.run``), or of a choice
  written in place (``(copy_tree if folder else copy_file)(source, target)``): each alternative is a call;
* a thread's target with its arguments written out (``Thread(target=f, args=(a, b))`` is ``f(a, b)``).

Reported (``dynamic_references``), since the reader can't tell what runs or with what: a sink function or method, or
an add-on helper that passes a parameter to a sink, referenced without a call the reader follows (stored on an
object, passed as a callable, ``functools.partial``, a thread's target without written arguments), ``getattr`` or
``operator.methodcaller`` naming a sink method, and a ``getattr`` result with a computed name called, unless every
function it can name is the add-on's own and none is such a helper (``getattr(self, "_task_" + name)``: each
candidate's own sinks are read in its body, where a parameter no call passes counts as state), or every name it can
be is known and none names a sink or such a helper (``for name in ("to_dict", "to_list")``, a module's fixed tuple
of names, a literal prefix no sink name starts with: ``"pgettext_" + kind``). A reference that is only compared
(``os.open in os.supports_dir_fd``) is no dispatch.
"""

import ast

import destructive_sinks as _d
from write_sink_calls import SINK_FUNCTIONS, SINK_METHODS, os_open_kind, own_nodes

THREADS = frozenset({"threading.Thread", "Thread"})
_STR_METHODS = ("str.", "bytes.")


def alternatives(expr):
    """The values an expression may be: each side of ``a or b`` and ``a if c else b``, else itself."""
    if isinstance(expr, ast.BoolOp):
        return [alt for value in expr.values for alt in alternatives(value)]
    if isinstance(expr, ast.IfExp):
        return alternatives(expr.body) + alternatives(expr.orelse)
    return [expr]


def callable_aliases(node):
    """``({name: [callable expressions it may hold]}, {id of each node followed})`` of a function: parameters with a
    callable default and locals assigned callables (``x = x or sink``). Only names and dotted names count."""
    aliases, followed = {}, set()
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        args = node.args
        positional = args.posonlyargs + args.args
        pairs = list(zip(positional[len(positional) - len(args.defaults):], args.defaults))
        pairs += [(a, d) for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is not None]
        for arg, default in pairs:
            found = [alt for alt in alternatives(default) if isinstance(alt, (ast.Name, ast.Attribute))
                     and not (isinstance(alt, ast.Name) and alt.id == "None")]
            if found:
                aliases[arg.arg] = found
    for inner in own_nodes(node):
        if (isinstance(inner, ast.Assign) and len(inner.targets) == 1 and isinstance(inner.targets[0], ast.Name)
                and isinstance(inner.value, (ast.BoolOp, ast.IfExp))):
            found = []
            for alt in alternatives(inner.value):
                if isinstance(alt, ast.Name) and alt.id in aliases:
                    found += aliases[alt.id]
                elif isinstance(alt, (ast.Name, ast.Attribute)):
                    found.append(alt)
            name = inner.targets[0].id
            if found and any(_called_name(n, name) for n in own_nodes(node)):
                aliases[name] = aliases.get(name, []) + found
                followed |= {id(alt) for alt in alternatives(inner.value)}
    for found in aliases.values():
        followed |= {id(alt) for alt in found}
    return aliases, followed


def _called_name(node, name):
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name


def call_views(call, aliases, canonical):
    """``(calls a call stands for, ids of the callables it follows)``: itself, or one per callable it may run, and
    a thread's target with its written arguments."""
    func, followed = call.func, set()
    choices = None
    if isinstance(func, (ast.BoolOp, ast.IfExp)):
        choices = [alt for alt in alternatives(func) if isinstance(alt, (ast.Name, ast.Attribute))]
    elif isinstance(func, ast.Name) and func.id in aliases:
        choices = aliases[func.id]
    views = [call]
    if choices is not None:
        views = [_view(call, alt, call.args, call.keywords) for alt in choices]
        followed |= {id(alt) for alt in choices}
    if canonical(_d._dotted(func) or "") in THREADS or (_d._dotted(func) or "") in THREADS:
        keywords = {k.arg: k.value for k in call.keywords}
        target, args = keywords.get("target"), keywords.get("args")
        if isinstance(target, (ast.Name, ast.Attribute)) and (args is None or isinstance(args, (ast.Tuple, ast.List))):
            given = keywords.get("kwargs")
            named = ([ast.keyword(arg=k.value, value=v) for k, v in zip(given.keys, given.values)
                      if isinstance(k, ast.Constant)] if isinstance(given, ast.Dict) else [])
            if given is None or isinstance(given, ast.Dict):
                views.append(_view(call, target, list(args.elts) if args is not None else [], named))
                followed.add(id(target))
    return views, followed


def _view(call, func, args, keywords):
    view = ast.Call(func=func, args=args, keywords=keywords)
    view.lineno, view.col_offset = call.lineno, call.col_offset
    return view


def _bound(node):
    """Names a scope binds itself (parameters and assignments): never a module's sink function of that name."""
    names = set()
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        args = node.args
        names |= {a.arg for a in args.posonlyargs + args.args + args.kwonlyargs}
        names |= {a.arg for a in (args.vararg, args.kwarg) if a is not None}
    nodes = own_nodes(node) if not isinstance(node, ast.Module) else module_nodes(node)
    names |= {n.id for n in nodes if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del))}
    return names


def dynamic_references(analysis, module, qualname, node, helpers, followed=frozenset()):
    """``(line, what)`` of every place in ``node``'s own code that reaches a sink through dispatch the reader can't
    follow (the module docstring's list). ``helpers``: add-on functions that pass a parameter to a sink."""
    nodes = list(own_nodes(node)) if not isinstance(node, ast.Module) else list(module_nodes(node))
    parents = {id(child): parent for parent in nodes for child in ast.iter_child_nodes(parent)}
    bound = _bound(node)
    found = []
    computed = {}   # Locals assigned ``getattr(x, <computed name>)``: their getattr call.
    for inner in nodes:
        if isinstance(inner, ast.Assign) and _computed_getattr(inner.value):
            computed.update({t.id: inner.value for t in inner.targets if isinstance(t, ast.Name)})
    for inner in nodes:
        if isinstance(inner, ast.Call):
            dotted = _d._dotted(inner.func) or ""
            if dotted in ("getattr", "operator.methodcaller", "methodcaller") and inner.args:
                name = inner.args[1] if dotted == "getattr" and len(inner.args) > 1 else (
                    inner.args[0] if dotted != "getattr" else None)
                if isinstance(name, ast.Constant) and name.value in SINK_METHODS:
                    found.append((inner.lineno, "%s(%s)" % (dotted, name.value)))
            lookup = inner.func if _computed_getattr(inner.func) else (
                computed.get(inner.func.id) if isinstance(inner.func, ast.Name) else None)
            if (lookup is not None and not _only_own_functions(analysis, module, qualname, lookup, helpers)
                    and _may_name_a_sink(analysis, module, nodes, lookup.args[1], helpers)):
                found.append((inner.lineno, "getattr(<computed>)()"))
            continue
        if (not isinstance(inner, (ast.Name, ast.Attribute)) or not isinstance(inner.ctx, ast.Load)
                or id(inner) in followed):
            continue
        parent = parents.get(id(inner))
        if (isinstance(parent, (ast.Attribute, ast.Compare)) or (isinstance(parent, ast.Call) and parent.func is inner)
                or (isinstance(inner, ast.Name) and inner.id in bound)):
            continue   # Part of a longer name, only compared, a call the reader follows, or a local of that name.
        dotted = _d._dotted(inner)
        if dotted is None:
            continue
        name = analysis.canonical(module, dotted)
        target = _d._resolve(analysis.modules, module, qualname, inner)
        if target is not None:
            if target in helpers:
                found.append((inner.lineno, "%s.%s" % target))
            continue
        if name in SINK_FUNCTIONS or dotted in SINK_FUNCTIONS:
            found.append((inner.lineno, name))
        elif isinstance(inner, ast.Attribute) and inner.attr in SINK_METHODS and not dotted.startswith(
                _STR_METHODS):
            found.append((inner.lineno, "." + inner.attr))
    return sorted(set(found))


def _computed_getattr(expr):
    return (isinstance(expr, ast.Call) and _d._dotted(expr.func) == "getattr" and len(expr.args) > 1
            and not isinstance(expr.args[1], ast.Constant))


def _may_name_a_sink(analysis, module, nodes, expr, helpers):
    """Whether a computed attribute name may name a sink function or method, or an add-on helper: always, unless
    every name it can be is a known literal (a local iterated or assigned only from literal strings or a module's
    fixed tuple of them) or it starts with a literal prefix, and none of those names (or prefix) fits a sink."""
    sinks = SINK_METHODS | {name.rsplit(".", 1)[-1] for name in SINK_FUNCTIONS} | {
        qualname.rsplit(".", 1)[-1] for _module, qualname in helpers}
    prefix = _prefix(expr)
    if prefix:
        return any(name.startswith(prefix) for name in sinks)
    names = _literal_names(analysis, module, nodes, expr)
    return names is None or bool(names & sinks)


def _literal_names(analysis, module, nodes, expr):
    """The literal strings ``expr`` can be, or None when any value is not one."""
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return {expr.value}
    if isinstance(expr, (ast.Tuple, ast.List, ast.Set)):
        parts = [_literal_names(analysis, module, nodes, e) for e in expr.elts]
        return None if any(p is None for p in parts) else set().union(*parts)
    if not isinstance(expr, ast.Name):
        return None
    sources = [n.iter for n in nodes if isinstance(n, (ast.For, ast.comprehension)) and isinstance(n.target, ast.Name)
               and n.target.id == expr.id]
    sources += [n.value for n in nodes if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == expr.id for t in n.targets)]
    if any(isinstance(n, ast.arg) and n.arg == expr.id for n in nodes):
        return None   # A parameter: whatever a caller passes.
    if not sources:   # A module's fixed tuple of names (``CONVERTERS = ("tolist", ...)``).
        if expr.id not in analysis.constants.get(module.name, ()):
            return None
        from write_sinks_constants import bindings
        sources = [bindings(module.tree)[1].get(expr.id)]
    found = set()
    for source in sources:
        names = _literal_names(analysis, module, nodes, source) if source is not None else None
        if names is None:
            return None
        found |= names
    return found


def _prefix(expr):
    """The literal start of a computed name (``"_task_" + name``, ``f"_on_{kind}"``), or ""."""
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        return _prefix(expr.left) if not isinstance(expr.left, ast.Constant) else str(expr.left.value)
    if isinstance(expr, ast.JoinedStr) and expr.values and isinstance(expr.values[0], ast.Constant):
        return str(expr.values[0].value)
    return ""


def _only_own_functions(analysis, module, qualname, lookup, helpers):
    """Whether ``getattr(receiver, name)`` can name only functions of the add-on (``self``'s class, an add-on
    module or class), none of them a helper that passes a parameter to a sink."""
    receiver, prefix = lookup.args[0], _prefix(lookup.args[1])
    if not isinstance(receiver, ast.Name):
        return False
    if receiver.id in ("self", "cls") and "." in qualname:
        owner, functions = module, qualname.rsplit(".", 1)[0] + "."
    else:
        source = module.imports.get(receiver.id)
        if source is not None and source[1] is None and source[0] in analysis.modules:
            owner, functions = analysis.modules[source[0]], ""
        elif any(name.startswith(receiver.id + ".") for name in module.functions):
            owner, functions = module, receiver.id + "."
        else:
            return False
    candidates = [name for name in owner.functions if name.startswith(functions)
                  and "." not in name[len(functions):] and name[len(functions):].startswith(prefix)]
    return bool(candidates) and not any((owner.name, name) in helpers for name in candidates)


def program_of(node, expr):
    """The program a process start runs when its argv is a local list (``command = [binary, "-i", source]``):
    that list's first element (every literal list assigned to the name), never the rest of the arguments."""
    if not isinstance(expr, ast.Name):
        return expr
    assigns, indexed = [], False
    for n in own_nodes(node):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == expr.id:
                    assigns.append(n.value)
                elif isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) and t.value.id == expr.id:
                    indexed = True   # ``command[0] = ...``: the program changed after the list was built.
        elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "insert"
              and isinstance(n.func.value, ast.Name) and n.func.value.id == expr.id):
            indexed = True   # ``command.insert(0, ...)``: likewise.
    if indexed or not assigns or not all(isinstance(v, (ast.List, ast.Tuple)) and v.elts for v in assigns):
        return expr   # Not only literal lists: the whole value, as before (never less checked).
    firsts = [v.elts[0] for v in assigns]
    return firsts[0] if len(firsts) == 1 else ast.Tuple(elts=firsts, ctx=ast.Load())

def helper_arguments(analysis, target, call, flags_text):
    module = analysis.modules[target[0]]
    node = module.functions[target[1]]
    names = [a.arg for a in node.args.posonlyargs + node.args.args]
    offset = 1 if names and names[0] in ("self", "cls") and "." in target[1] else 0
    defaults = dict(zip(names[len(names) - len(node.args.defaults):], node.args.defaults))
    defaults.update({a.arg: d for a, d in zip(node.args.kwonlyargs, node.args.kw_defaults) if d is not None})

    def argument(name):
        given = next((k.value for k in call.keywords if k.arg == name), None)
        if given is None and name in names:
            index = names.index(name) - offset
            if 0 <= index < len(call.args):
                given = call.args[index]
        return given
    out = []
    for name, kinds in sorted(analysis.helpers[target].items()):   # Every kind a parameter reaches, in one order.
        given = argument(name)
        if given is None:
            continue
        for kind in sorted(kinds):
            if kind.startswith("os.open?"):   # A write only when this caller's flags write (or its own decide).
                flag, _, rest = kind[len("os.open?"):].partition(" ")
                flags = argument(flag)
                decided = os_open_kind(flags_text(flags) if flags is not None else (
                    ast.unparse(defaults[flag]) if flag in defaults else "?unknown"))
                if decided is None:
                    continue
                kind = decided + (" " + rest if rest else "")
            expr = given
            if kind.startswith("run:") and isinstance(expr, (ast.List, ast.Tuple)) and expr.elts:
                expr = expr.elts[0]   # A program started through a helper: argv[0] is the program.
            out.append(("%s via %s.%s" % (kind, target[0], target[1]), expr))
    return out


def module_nodes(tree):
    """A module's own top-level code: never inside a function or class body (each analyzed as its own)."""
    stack = list(ast.iter_child_nodes(tree))
    while stack:
        inner = stack.pop()
        yield inner
        if not isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            stack.extend(ast.iter_child_nodes(inner))
        elif isinstance(inner, ast.ClassDef):   # A class body's own statements run at import, like the module's.
            stack.extend(child for child in inner.body
                         if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
            stack.extend(inner.decorator_list)
