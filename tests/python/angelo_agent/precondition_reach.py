# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a tool's precondition can raise: a static walk of the add-on's call graph (``test_native_precondition_refusal_codes``).

A call's precondition (``BlenderExecutor.precondition`` and every owner's ``precondition``) runs before anything of the
call does: at dispatch, at its run and in native staging. The walk starts at every function or method named
``precondition``, follows the calls it can resolve (a module's own functions, ``from .x import y`` names, ``module.f``,
``self.method`` through the class's bases, a class's ``__init__``, and an object's method only one add-on class defines
under a name no builtin container, path or Blender collection uses), and reports every ``raise`` that carries no
closed refusal code (``refusal_codes``), and every ``next()`` without a default (a ``StopIteration``). What a name or
an object resolves to is ``precondition_package``'s reading of the add-on.

A raise carries a code when the call names one (``code=``), when it is ``coded(...)``, or when its exception class
carries one itself (a class whose body names a code, like ``SculptPreflightError`` or ``FlowError``; ``RefusalError``
itself defaults to UNKNOWN). A raise that a handler on every path to it stops is not reported (a handler that only
re-raises stops nothing); neither is one in the body of a try whose handler catches everything and raises nothing,
nor one inside a ``BOUNDARIES`` context manager, which classifies whatever leaves it. Standard library only.
"""

import ast

from precondition_package import (CATCH_ALL, FUNCTIONS, PLAIN, Package,  # noqa: F401  (Package: the test's)
                                  local_values, object_class, resolve_call)

# Context managers that give every exception leaving their body a closed code (sculpt_preflight.before_an_edit).
BOUNDARIES = {"before_an_edit"}


def _types(handler):
    if handler.type is None:
        return {"BaseException"}
    nodes = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    names = {getattr(node, "id", getattr(node, "attr", None)) for node in nodes}
    # Keep exact qualified spellings for external classes the source walk cannot resolve.
    names.update("qualified:" + ast.dump(node) for node in nodes
                 if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name))
    return names


def _silent(handler):
    """A handler that catches everything and raises nothing: what its try body raises never escapes."""
    return bool(_types(handler) & CATCH_ALL) and not any(isinstance(node, ast.Raise) for node in ast.walk(handler))


def _reraises(handler):
    """Whether a handler can raise again what it caught (a bare ``raise``, or ``raise`` of its own name)."""
    return any(isinstance(node, ast.Raise) and (node.exc is None or isinstance(node.exc, ast.Name)
                                                and node.exc.id == handler.name) for node in ast.walk(handler))


def _stopped_by(handlers):
    """Exception names the enclosing handlers stop, in their order: a handler that can re-raise what it caught (cleanup
    then ``raise``) stops nothing, and no later handler of its try is reached for what it catches, so the walk takes
    none of them."""
    names, tries = set(), {}
    for handler in handlers:
        tries.setdefault(id(getattr(handler, "_try", None)), []).append(handler)
    for group in tries.values():
        for handler in group:
            if _reraises(handler):
                break
            names |= _types(handler)
    return frozenset(names)


def _name(call):
    return call.func.id if isinstance(call.func, ast.Name) else getattr(call.func, "attr", None)


def body(function):
    """(node, enclosing handlers) of ``function``'s own code: never a lambda, a nested class, a silent try's body or
    a boundary's body; a nested function where it is called."""
    nested = {node.name: node for node in ast.walk(function) if isinstance(node, FUNCTIONS) and node is not function}
    called, out = set(), []

    def visit(node, handlers):
        if isinstance(node, (ast.Lambda, ast.ClassDef)) or isinstance(node, FUNCTIONS) and node is not function:
            return
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                handler._try = node   # _stopped_by reads each try's handlers in order.
            if not any(_silent(handler) for handler in node.handlers[:next(
                    (i for i, h in enumerate(node.handlers) if _reraises(h)), len(node.handlers))]):
                for child in node.body:
                    visit(child, handlers + node.handlers)
            for child in node.handlers + node.orelse + node.finalbody:
                visit(child, handlers)
            return
        if isinstance(node, (ast.With, ast.AsyncWith)) and any(
                isinstance(item.context_expr, ast.Call) and _name(item.context_expr) in BOUNDARIES for item in node.items):
            return
        out.append((node, handlers))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in nested \
                and node.func.id not in called:
            called.add(node.func.id)
            for child in nested[node.func.id].body:
                visit(child, handlers)
        for child in ast.iter_child_nodes(node):
            visit(child, handlers)

    for child in function.body:
        visit(child, [])
    return out


def callees(package, key):
    """((module, qualified name), names the call site's handlers stop) for each call ``key`` makes that resolves."""
    module, qualified = key
    cls = qualified.split(".")[0] if "." in qualified else None
    function, out = package.node(key), set()
    for node, handlers in body(function):
        if not isinstance(node, ast.Call):
            continue
        stops, func, found = _stopped_by(handlers), node.func, None
        if isinstance(func, ast.Name):
            found = package.resolve(module, func.id)
        elif isinstance(func, ast.Attribute):
            owner = package.resolve(module, func.value.id) if isinstance(func.value, ast.Name) else None
            receiver = None if owner and owner[0] == "module" else object_class(package, module, cls, function,
                                                                             func.value)
            if owner and owner[0] == "module":
                found, method = package.resolve(owner[1], func.attr), None
            elif receiver:
                method = package.method(receiver[0], receiver[1], func.attr)
            else:
                method = package.unique.get(func.attr)   # An object's method only one add-on class defines.
            if method:
                out.add((method, stops))
        if found and found[0] == "func":
            out.add(((found[1], found[2]), stops))
        elif found and found[0] == "class":
            for init in ("__init__", "__post_init__"):
                method = package.method(found[1], found[2], init)
                if method:
                    out.add((method, stops))
    return out


def roots(package):
    """Every function and method named ``precondition``."""
    out = set()
    for module in package.trees:
        if "precondition" in package.functions[module]:
            out.add((module, "precondition"))
        for cls, node in package.classes[module].items():
            if any(isinstance(item, FUNCTIONS) and item.name == "precondition" for item in node.body):
                out.add((module, cls + ".precondition"))
    return out


def reach(package, start, stop=lambda key: False):
    """Every function reached from ``start`` (``stop`` ends a path), its first caller, and the exception names a
    handler stops on every path to it (a greatest fixed point over the call graph)."""
    seen, parent, stack, edges = set(), {}, sorted(start), {}
    while stack:
        key = stack.pop()
        if key in seen or stop(key) or package.node(key) is None:
            continue
        seen.add(key)
        for callee, stops in callees(package, key):
            edges.setdefault(callee, []).append((key, stops))
            if callee not in seen:
                parent.setdefault(callee, key)
                stack.append(callee)
    stopped = {key: frozenset() if key in start else None for key in seen}   # None: not yet bounded (everything).
    changed = True
    while changed:
        changed = False
        for key in seen - set(start):
            paths = [stops | stopped[caller] for caller, stops in edges.get(key, ())
                     if caller in seen and stopped[caller] is not None]
            value = frozenset.intersection(*paths) if paths else None
            if value is not None and value != stopped[key]:
                stopped[key], changed = value, True
    return seen, parent, {key: value or frozenset() for key, value in stopped.items()}


def uncoded(package, key, stopped=frozenset()):
    """(line, what) of each raise in ``key`` that carries no closed code, and each ``next()`` without a default."""
    module, function, found = key[0], package.node(key), []
    for node, handlers in body(function):
        local_stops = _stopped_by(handlers)
        stops = stopped | local_stops
        if isinstance(node, ast.Raise) and node.exc is not None:
            for value in _built_values(node.exc, function):
                found += [(node.lineno, problem) for problem in _judge(
                    package, module, value, function, stops, qualified_stops=local_stops)]
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "next"
              and len(node.args) == 1 and not {"StopIteration", "Exception", "BaseException"} & stops):
            found.append((node.lineno, "next() without a default"))
    return found


def _built_values(value, function):
    """The exception constructions a raised expression stands for: itself when it is a call, or what a local name
    was assigned (``failure = X(...); raise failure``); none for a caught or handed exception (``raise error``), nor
    for a name the function gives a code itself (``error.refusal_code = ...``, as ``coded`` does)."""
    if isinstance(value, ast.Call):
        return [value]
    if isinstance(value, ast.Name) and not _codes_itself(function, value.id):
        assigned = local_values(function, value.id)
        return [v for v in assigned if isinstance(v, ast.Call)] if assigned else []
    return []


def _codes_itself(function, name):
    return any(isinstance(node, ast.Assign) and any(
        isinstance(t, ast.Attribute) and t.attr == "refusal_code" and isinstance(t.value, ast.Name) and t.value.id == name
        for t in node.targets) and not (isinstance(node.value, ast.Constant) and node.value.value == "UNKNOWN")
        for node in ast.walk(function))


def _passed_on(function, value):
    """Whether a returned value is not built here: None, a caught exception (``except X as name``) or a parameter."""
    if isinstance(value, ast.Constant) and value.value is None:
        return True
    if not isinstance(value, ast.Name):
        return False
    caught = {node.name for node in ast.walk(function) if isinstance(node, ast.ExceptHandler) and node.name}
    return value.id in caught


def _keyword_code(call):
    """None without a ``code=``/``refusal_code=``; else whether it may be a closed code (a constant UNKNOWN is not)."""
    keyword = next((k for k in call.keywords if k.arg in ("code", "refusal_code")), None)
    if keyword is None:
        return None
    return not (isinstance(keyword.value, ast.Constant) and keyword.value.value == "UNKNOWN")


def _judge(package, module, call, function, stops, depth=0, *, qualified_stops=frozenset()):
    """What is uncoded about the exception ``call`` builds (an empty list when it carries a closed code)."""
    name = _name(call)
    if name == "coded" or depth > 4:
        return []
    keyword = _keyword_code(call)
    if keyword is not None:
        return [] if keyword else ["%s(code=UNKNOWN)" % name]
    if any(isinstance(a, ast.Constant) and a.value in package.codes - {"UNKNOWN"} for a in call.args[1:]):
        return []   # A code passed by position (``FetchError(message, "CANCELLED")``).
    kind = resolve_call(package, module, call.func)
    if (kind is None and isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name)
            and "qualified:" + ast.dump(call.func) in qualified_stops):
        return []   # Local handler only: an inherited alias may name another module's external class.
    if kind is None and isinstance(call.func, ast.Name) and function is not None:
        # A function its caller may pass, defaulting to one of the module's (``admit(name, check=refusal)``).
        arguments = function.args
        defaults = dict(zip([a.arg for a in arguments.args][-len(arguments.defaults):], arguments.defaults)) \
            if arguments.defaults else {}
        defaults.update({a.arg: d for a, d in zip(arguments.kwonlyargs, arguments.kw_defaults) if d is not None})
        if isinstance(defaults.get(call.func.id), ast.Name):
            kind = package.resolve(module, defaults[call.func.id].id)
    if kind and kind[0] == "class" or kind is None and name in PLAIN:
        names = package.class_names(kind[1], kind[2]) if kind else package.names(module, name)
        if names & stops:
            return []
        return [] if kind and package.coded_raise(kind[1], kind[2], call, function) else [name]
    if not (kind and kind[0] == "func"):
        return ["%s(), which the walk cannot resolve" % name]
    # A helper that builds or passes on the exception: judge what it returns. One that returns what it is handed
    # (``nothing_ran(error)``) is judged by the argument it is handed here.
    helper = package.node(kind[1:])
    parameters = [argument.arg for argument in helper.args.args]
    problems = []
    returns = [node.value for node in ast.walk(helper) if isinstance(node, ast.Return) and node.value is not None]
    while any(isinstance(value, ast.IfExp) for value in returns):   # ``return None if ... else refusal(...)``
        returns = [part for value in returns for part in ((value.body, value.orelse) if isinstance(value, ast.IfExp)
                                                          else (value,))]
    for returned in returns:
        if isinstance(returned, ast.Name) and returned.id in parameters:
            index = parameters.index(returned.id)
            handed = call.args[index] if index < len(call.args) else next(
                (k.value for k in call.keywords if k.arg == returned.id), None)
            values = _built_values(handed, function) if handed is not None else []
            if not values and not isinstance(handed, ast.Name):
                problems.append("%s(), whose argument the walk cannot judge" % name)
            for value in values:
                problems += _judge(package, module, value, function, stops, depth + 1)
            continue
        if _passed_on(helper, returned):
            continue   # Nothing built here: no refusal, or one judged where it was raised.
        values = _built_values(returned, helper)
        if not values and not (isinstance(returned, ast.Name) and _codes_itself(helper, returned.id)):
            problems.append("%s(), whose return the walk cannot judge" % name)
        for value in values:
            problems += ["%s() returning %s" % (name, problem)
                         for problem in _judge(package, kind[1], value, helper, stops, depth + 1)]
    return problems


def chain(parent, key, limit=8):
    """``key``'s first path back to a precondition, for a readable failure."""
    out = []
    while key in parent and len(out) < limit:
        key = parent[key]
        out.append("%s.%s" % key)
    return " <- ".join(out)
