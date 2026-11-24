# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where Angelo's own UI calls a grant operator, and whether the person's own press reaches it (grant_operators.rst).

A grant operator (``grant_guard.guard``) takes effect only in a C grant scope: the ticket of the person's press on the
control that asked for it (``grant_guard.ticketed`` / ``by_click``; kept for a later step or a drag by
``grant_gestures``), Angelo's own automatic decision (``grant_authority.automatic``, ``grant_guard.automatic``), or the
guarded operator's own body (it runs only once admitted). A surface that calls one from a click outside such a scope
refuses the person too: the 2.0.0 Media Studio Generate and Enhance, the Flows bar's Run and Studio Workflows' run,
check and fit did (found by an-blender53's Mac run of 3becd249274). Openers match by their exact owner and name; an
automatic one covers a call only at a site declared in ``AUTOMATIC`` with why no press is needed there, so a UI surface
can't pass for covered by Angelo's own decision (the independent review of 9c8b4e57077). Standard library only: the host
test ``test_native_grant_call_sites`` reads the source.

``call_sites()`` finds every call of a guarded operator: ``bpy.ops.angelo.<name>(...)``, ``grant_guard.clicked()``'s,
and a ``getattr`` of either by a name read at run time (unless every name it can read is a literal of a module-level
table, none of them a grant). ``uncovered()`` follows each call's function back through its callers: a function is
covered when it is passed to (or called inside a lambda passed to) a scope opener (above), is a method of a guarded
operator, is declared in ``NOT_PERSON`` with why no person's click reaches it, or every call of it is covered (passed to
an opener, in a lambda one calls, or in a covered function): one direct call from a key handler outside a scope is
enough to refuse the person. A function with no caller the scan can see is not covered: a new call site has to say how
the person's press reaches it.
"""

import ast

import grant_inventory as inventory

# The person's press: each runs its function argument in the grant scope of that press's C ticket (if it minted one).
PERSON_OPENERS = frozenset({("grant_guard", "ticketed"), ("grant_guard", "by_click"), ("grant_gestures", "deferred"),
                            ("grant_gestures", "press_hold"), ("grant_gestures", "dragged")})
# Angelo's own decision (no press): covers a call only at a site declared in AUTOMATIC (the review of 9c8b4e57077).
AUTOMATIC_OPENERS = frozenset({("grant_authority", "automatic"), ("grant_guard", "automatic")})
GUARDS = frozenset({"guard", "grant"})   # grant_guard.guard(Cls, ...) and @grant_guard.grant(...) mark grant operators.

# "module:function" -> why Angelo's own decision, not a press, may open a grant scope there.
AUTOMATIC = {
    "context_folder_grants:Grants._set_state": "a context folder's state in C (its attach order, created count, legacy "
                                               "mark, the agent's own files) after the person's attach or switch, or "
                                               "after a write of the agent's: refused while agent code runs",
    "context_folder_grants:Grants.remove": "the working folder moves on to the next non-legacy folder the person "
                                           "attached when they remove the working one (agent_chat.rst); never while "
                                           "agent code runs, and the chat says which folder is working now",
    "script_admission:prepare": "admits the exact script text the chat's approval route approved",
    "trusted_scripts:TrustedScripts._approve_automatically": "a script a trusted-scripts rule covers, as the person "
                                                             "set it",
    "trusted_scripts_ui:always_allow_set": "the Preferences toggle, after grant_guard.person_set took the press's "
                                           "ticket (on), or turning it off (narrowing)",
    "ui:pump.apply_mutation": "the chat applies the mutation its approved call made",
    "worker_chat_host:WorkerChatHost.apply_mutation": "a worker chat applies its approved call's mutation",
    "worker_chat_host:WorkerChatHost._open": "a worker chat takes the options its parent chat already holds",
    "worker_chat_host:WorkerChatHost._options": "a worker chat follows its parent chat's options",
    "worker_consent:approve": "a worker reviews the script its parent chat's person approved",
    "worker_dispatch:apply": "a worker runs the call its parent chat approved",
    "worker_host:WorkerHost.edit.apply_mutation": "a worker applies its approved call's mutation",
    "worker_host:WorkerHost.edit": "a worker reviews the script its parent chat's person approved",
}

# "module:function" -> why no person's click reaches a grant operator from it (its call refuses, by design).
NOT_PERSON = {
    "app_feature_host:_engine_link": "an agent's request to set up an engine link: the folder picker it would open "
                                     "is a grant, so it refuses there and the agent tells the person where to click "
                                     "(the + menu's row or the engine suggestion card)",
    "layout_after:again.action": "re-runs, once the layout settles, the operator that asked for it: only Media "
                                 "Studio's open and toggle (studio_toggle), which grant nothing",
}


def _opener(call):
    """"person", "automatic" or None: whether ``call`` opens a grant scope, by its exact owner and name."""
    if not isinstance(call.func, ast.Attribute):
        return None
    pair = (ast.unparse(call.func.value), call.func.attr)
    return "person" if pair in PERSON_OPENERS else "automatic" if pair in AUTOMATIC_OPENERS else None


def _is_opener(call, key=None, function=None):
    """Whether ``call`` opens a grant scope a guarded call may run in: a person's press anywhere, Angelo's own
    decision only at a declared AUTOMATIC site."""
    kind = _opener(call)
    return kind == "person" or (kind == "automatic" and "%s:%s" % (key, function[1] if function else "<module>")
                                in AUTOMATIC)


def automatic_sites(index=None):
    """{"module:function"} of every call of an automatic opener (each must be declared in AUTOMATIC)."""
    index = index or _Index()
    return {"%s:%s" % (key, function[1] if function else "<module>")
            for key, function, call, _opened in index.calls if _opener(call) == "automatic"}


def guarded_classes():
    """{"module:Class"} of every operator class the add-on guards (``grant_guard.guard`` / ``@grant_guard.grant``)."""
    found = set()
    for key, tree in inventory.sources():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in GUARDS \
                    and "grant_guard" in ast.unparse(node.func.value):
                found.update("%s:%s" % (key, arg.id) for arg in node.args if isinstance(arg, ast.Name))
            elif isinstance(node, ast.ClassDef):
                for decorator in node.decorator_list:
                    if "grant_guard" in ast.unparse(decorator) and any(g in ast.unparse(decorator) for g in GUARDS):
                        found.add("%s:%s" % (key, node.name))
    return found


def guarded_names():
    """The operator names (``bpy.ops.angelo.<name>``) of the guarded classes."""
    classes, names = guarded_classes(), set()
    for key, tree in inventory.sources():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and "%s:%s" % (key, node.name) in classes:
                idname = inventory._class_idname(node) or ""
                if idname.startswith("angelo."):
                    names.add(idname.split(".", 1)[1])
    return names


class _Index:
    """Every function of the add-on with its module, class and qualified name, every call with the function it is in
    (and whether it runs inside a lambda a scope opener calls), the names each module imports from a sibling, and its
    module-level tables of literal strings."""

    def __init__(self):
        self.functions = {}   # (key, qualname) -> (key, qualname, class name or None, node)
        self.calls = []       # (key, enclosing function entry or None, call node, runs in an opener's scope)
        self.modules = {}     # (key, local name) -> module key (``from . import x [as y]``)
        self.names = {}       # (key, local name) -> (module key, name) (``from .x import a [as b]``)
        self.tables = {}      # (key, NAME) -> {literal strings} (``NAME = {"a": "op", ...}`` at module level)
        for key, tree in inventory.sources():
            for node in tree.body:
                if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                        and isinstance(node.value, ast.Dict) and node.value.values and all(
                            isinstance(v, ast.Constant) and isinstance(v.value, str) for v in node.value.values):
                    self.tables[(key, node.targets[0].id)] = {v.value for v in node.value.values}
            self._walk(key, tree, None, "", None, False)

    def _walk(self, key, node, function, path, cls, opened):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ImportFrom) and child.level == 1:
                for alias in child.names:
                    local = alias.asname or alias.name
                    if child.module is None:
                        self.modules[(key, local)] = alias.name
                    else:
                        self.names[(key, local)] = (child.module, alias.name)
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = path + child.name
                entry = (key, qualname, cls, child)
                self.functions[(key, qualname)] = entry
                self._walk(key, child, entry, qualname + ".", None, False)
            elif isinstance(child, ast.ClassDef):
                self._walk(key, child, function, path + child.name + ".", child.name, opened)
            elif isinstance(child, ast.Call):
                self.calls.append((key, function, child, opened))
                opener = _is_opener(child, key, function)
                for arg in list(child.args) + [k.value for k in child.keywords]:
                    # A lambda an opener calls runs in its scope; any other argument is read before the scope opens.
                    inside = opened or (opener and isinstance(arg, ast.Lambda))
                    self._walk(key, ast.Expr(value=arg), function, path, cls, inside)
                self._walk(key, ast.Expr(value=child.func), function, path, cls, opened)
            else:
                self._walk(key, child, function, path, cls, opened)

    def resolve(self, key, func):
        """The (module, name) targets a call's ``func`` may run: a sibling module's function, this module's own, a
        method of this class (``self.x``), or any method of that name (``obj.x``: ``("method", x)``)."""
        if isinstance(func, ast.Name):
            return [self.names.get((key, func.id), (key, func.id))]
        if isinstance(func, ast.Attribute):
            owner = ast.unparse(func.value)
            module = self.modules.get((key, owner))
            if module:
                return [(module, func.attr)]
            return [(key, func.attr)] if owner in ("self", "cls") else [("method", func.attr)]
        return []

    def table_values(self, key, node):
        """The literal strings a ``NAME[...]`` / ``module.NAME[...]`` subscript can read, or None if unknown."""
        if not isinstance(node, ast.Subscript):
            return None
        target = node.value
        if isinstance(target, ast.Name):
            module, name = self.names.get((key, target.id), (key, target.id))
        elif isinstance(target, ast.Attribute) and self.modules.get((key, ast.unparse(target.value))):
            module, name = self.modules[(key, ast.unparse(target.value))], target.attr
        else:
            return None
        return self.tables.get((module, name))


def _is_guarded_call(index, key, call, names):
    """Whether ``call`` runs a guarded operator: by name, or by a name read at run time (``getattr``)."""
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr in names:
        owner = ast.unparse(func.value)
        return owner == "bpy.ops.angelo" or "clicked(" in owner
    if isinstance(func, ast.Call) and getattr(func.func, "id", "") == "getattr" and len(func.args) > 1:
        owner = ast.unparse(func.args[0])
        # bpy.ops.angelo, Angelo's click path, a local ``ops`` of either, or an operator group read at run time
        # (``getattr(getattr(bpy.ops, group), name)``: a row's operator, the Flows menu's).
        if not (owner in ("bpy.ops.angelo", "ops") or "clicked(" in owner or owner.startswith("getattr(bpy.ops")):
            return False
        name = func.args[1]
        if isinstance(name, ast.Constant) and isinstance(name.value, str):
            return name.value in names
        values = index.table_values(key, name)
        return values is None or bool(values & names)
    return False


def call_sites(index=None):
    """[(module, function qualname or "<module>", source line, call text)] of every guarded operator call."""
    index, names = index or _Index(), guarded_names()
    return [(key, function[1] if function else "<module>", call.lineno, ast.unparse(call)[:100])
            for key, function, call, _opened in index.calls if _is_guarded_call(index, key, call, names)]


def uncovered():
    """[(module, function, line, call)] of guarded operator calls no person's press reaches through a grant scope."""
    index = _Index()
    sites, guarded = call_sites(index), guarded_classes()
    callers = {}   # (module or "method", name) -> [(caller entry or None, runs in a grant scope, caller's module)]
    passers = {}   # method name -> modules that pass ``obj.<name>`` to an opener
    for key, function, call, opened in index.calls:
        if _is_opener(call, key, function):   # Passed to an opener: that call of it runs in the scope.
            for arg in call.args:
                if isinstance(arg, (ast.Name, ast.Attribute)):
                    for target in index.resolve(key, arg):
                        callers.setdefault(target, []).append((function, True, key))
                        if target[0] == "method":
                            passers.setdefault(target[1], set()).add(key)
        for target in index.resolve(key, call.func):
            callers.setdefault(target, []).append((function, opened, key))
    memo = {}

    def covered(entry):
        key, qualname, cls, node = entry
        if (key, qualname) in memo:
            return memo[(key, qualname)]
        memo[(key, qualname)] = False   # A cycle covers nothing by itself.
        name = node.name
        result = "%s:%s" % (key, qualname) in NOT_PERSON or bool(cls and "%s:%s" % (key, cls) in guarded)
        if not result:   # Every way it is called runs in a grant scope (one direct call outside is enough to refuse).
            # ``obj.name(...)`` may be any method of that name: counted from this module, or from a module that also
            # passes ``obj.name`` to an opener (a surface that tickets a card's ``act`` in one place calls it in
            # another).
            found = callers.get((key, name), []) + ([c for c in callers.get(("method", name), [])
                                                     if c[2] == key or c[2] in passers.get(name, ())] if cls else [])
            result = bool(found) and all(opened or (caller is not None and covered(caller))
                                         for caller, opened, _module in found)
        memo[(key, qualname)] = result
        return result

    missing = []
    for key, qualname, line, text in sites:
        entry = index.functions.get((key, qualname))
        if entry is None or not covered(entry):
            missing.append((key, qualname, line, text))
    return missing
