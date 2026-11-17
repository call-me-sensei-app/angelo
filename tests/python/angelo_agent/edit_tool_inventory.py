# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every place the add-on hands Blender a callback that runs on the main thread: timers and app handlers.

``sites()`` finds each ``bpy.app.timers.register(callback, ...)`` and ``bpy.app.handlers.<list>.append(callback)``
in ``scripts/addons_core/angelo_agent`` by AST, keyed ``(module, enclosing function, kind, callback text)``, and says
whether the callback goes through ``edit_tool_hold.guarded`` (wrapped inline, a function or method decorated with it,
or an attribute assigned from it). ``test_native_edit_tool_inventory`` requires every unguarded one to be reviewed.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
GUARD = "edit_tool_hold.guarded"


def _qualified(tree):
    """``{node: qualified name}`` of every function, ``Class.method`` and nested ``outer.inner``."""
    names = {}

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = prefix + child.name
                names[child] = name
                walk(child, name + ".")
            else:
                walk(child, prefix)
    walk(tree, "")
    return names


def _decorated(function):
    return any(ast.unparse(item).startswith(GUARD) for item in function.decorator_list)


def _guarded_names(tree):
    """Names (``f``, ``Class.f``) and attributes (``self.x``) the module binds to a guarded callable."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _decorated(node):
            found.add(node.name)
        elif isinstance(node, ast.Assign) and ast.unparse(node.value).startswith(GUARD + "("):
            found.update(ast.unparse(target) for target in node.targets)
    return found


_TREES = {}


def _tree(module):
    if module not in _TREES:
        path = ADDON / module
        _TREES[module] = ast.parse(path.read_text(encoding="utf-8")) if path.exists() else None
    return _TREES[module]


def guarded(module, callback):
    """Whether ``callback`` (an AST expression in ``module``) runs through ``edit_tool_hold.guarded``."""
    text = ast.unparse(callback)
    if text.startswith(GUARD + "("):
        return True
    if isinstance(callback, ast.Attribute) and isinstance(callback.value, ast.Name) and callback.value.id != "self":
        other = _tree(callback.value.id + ".py")   # A function of another add-on module (``worker_sessions._tick``).
        return other is not None and callback.attr in _guarded_names(other)
    return text in _guarded_names(_tree(module))


# Generic deferral helpers whose callers pass any callback (a call to one is a registration), and where each lives
# (None: any module): card_host.Host.after, media_shelf._defer, settings_import_ui._later, edit_tool_hold._later,
# thread_start.daemon_or_later.
HELPERS = {"host.after": None, "_defer": {"media_shelf.py"}, "_later": {"settings_import_ui.py", "edit_tool_hold.py"},
           "daemon_or_later": None, "thread_start.daemon_or_later": None,
           # A live preview's handler on every list a scene change arrives on, and its edit notice (scene_changes).
           "scene_changes.watch": None, "scene_changes.on_edit": None}


def _kind(call, aliases, module="", handler_names=()):
    """``"timer"``, ``"handler:<list>"``, ``"msgbus"``, ``"draw"``, ``"helper:<name>"`` or None for a call node."""
    func = ast.unparse(call.func)
    if (func.endswith("timers.register") or func in aliases) and call.args:
        return "timer"
    parts = func.split(".")
    if len(parts) >= 3 and parts[-1] in {"append", "insert"} and parts[-3] == "handlers" and call.args:
        return "handler:" + parts[-2]
    receiver, _dot, method = func.rpartition(".")
    if method in {"append", "insert"} and call.args and ("app.handlers" in receiver or receiver in handler_names):
        return "handler:dynamic"   # ``getattr(bpy.app.handlers, name).append(f)``, or a name bound to one.
    if func.endswith("msgbus.subscribe_rna"):
        return "msgbus"
    if func.endswith(".draw_handler_add") and call.args:
        return "draw"
    if func.endswith(".host.after") and call.args:   # A card's host by any receiver (self.host, card.host).
        return "helper:host.after"
    if func in HELPERS and call.args and (HELPERS[func] is None or module in HELPERS[func]):
        return "helper:" + func
    return None


def _callback(call, kind):
    if kind == "msgbus":
        return next((keyword.value for keyword in call.keywords if keyword.arg == "notify"), call.func)
    if kind.startswith("helper:") and len(call.args) > 1 and kind == "helper:host.after":
        return call.args[1]
    return call.args[-1] if kind.startswith("handler:") else call.args[0]


def _handler_values(tree):
    """Names and ``self`` attributes bound to an expression that names ``bpy.app.handlers``."""
    return {ast.unparse(target) for node in ast.walk(tree) if isinstance(node, ast.Assign)
            and "handlers." in ast.unparse(node.value) for target in node.targets}


def sites():
    """``[(key, guarded)]``: key ``(module, enclosing function or "<module>", kind, callback text[:80])``. Every timer
    (``timers.register`` or a name bound to it), app handler (appended or inserted, directly or from a loop over
    handler lists), msgbus subscription, draw handler and call of a generic deferral helper (``HELPERS``)."""
    found = []
    for path in sorted(ADDON.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        owners = _qualified(tree)
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        aliases = {ast.unparse(target) for node in ast.walk(tree) if isinstance(node, ast.Assign)
                   and ast.unparse(node.value).endswith("timers.register") for target in node.targets}
        handler_values = _handler_values(tree)
        handler_names = {ast.unparse(target) for node in ast.walk(tree) if isinstance(node, ast.Assign)
                         and "app.handlers" in ast.unparse(node.value) for target in node.targets}

        def owner_of(node):
            walker = node
            while walker in parents:
                walker = parents[walker]
                if walker in owners:
                    return owners[walker]
            return "<module>"
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                kind = _kind(node, aliases, path.name, handler_names)
                if kind is not None:
                    callback = _callback(node, kind)
                    found.append(((path.name, owner_of(node), kind, ast.unparse(callback)[:80]),
                                  guarded(path.name, callback)))
            elif isinstance(node, ast.For):   # ``for handlers, f in <handler lists>: handlers.append(f)``.
                source = ast.unparse(node.iter)
                if "handlers." in source or source in handler_values:
                    if any(isinstance(call, ast.Call) and ast.unparse(call.func).endswith((".append", ".insert"))
                           for call in ast.walk(node)):
                        found.append(((path.name, owner_of(node), "handler-loop", source[:80]), False))
    return found


GATE_CALLS = ("edit_tool_hold.running", "edit_tool_hold.guarded")


def _gates(node):
    return any(isinstance(call, ast.Call) and ast.unparse(call.func) in GATE_CALLS for call in ast.walk(node))


def modals():
    """``[(key, guarded)]`` for each operator ``modal(self, context, event)`` of the add-on, keyed ``(module, class)``:
    guarded when the method, or a method of its class it calls on ``self``, asks the gate (break-it review 3: an
    Angelo modal that works on TIMER events, which reach every handler during the person's operation)."""
    found = []
    for path in sorted(ADDON.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for owner, name in _qualified(tree).items():
            if not isinstance(owner, ast.ClassDef):
                continue
            methods = {item.name: item for item in owner.body if isinstance(item, ast.FunctionDef)}
            modal = methods.get("modal")
            if modal is None or len(modal.args.args) < 3:
                continue
            called = {ast.unparse(call.func)[5:] for call in ast.walk(modal) if isinstance(call, ast.Call)
                      and ast.unparse(call.func).startswith("self.")}
            guarded = _gates(modal) or any(_gates(methods[method]) for method in called if method in methods)
            found.append(((path.name, name), guarded))
    return found
