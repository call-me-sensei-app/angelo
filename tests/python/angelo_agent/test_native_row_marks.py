# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every change in place to a chat row in the add-on's source is marked, so the pump's owed save writes it (B1).

The owed save (``tool_finalization.save_if_owed``) encodes only rows new or marked since the last write
(``session_writer``). A row changed in place and never marked waits for the scrub: the B1 break-it review found a
restore's notice (``checkpoint_restore_ui``, a reference kept from ``_safety_notice`` and changed after the scene
opened) whose outcome a crash then lost. This audit reads every module of the add-on and finds each change to a
row object: a value taken from a chat's row list (``<x>["messages"]``, an alias of it, an iteration over it,
``next(...)`` over it), a value put into one (``append``/``insert``/``extend``) and later changed, a value a
function returns after putting it there, and a parameter such a row is passed to. Each change must be

* made before the row joins the chat (a new row is built, then appended), or
* inside ``with changing(controller, row)`` (``transcript_rows``: under the lock, then marked), or
* inside a ``with <x>.lock:`` block that marks it after the change (``_touched``, ``_change_row``) or rewrites the
  journal whole (``invalidate``), or
* the marking helpers' own (``transcript_rows``).

Host tests also check at run time (``session_writer.CHECK_TOUCHES``) what this source audit cannot see.
"""

import ast
import unittest
from pathlib import Path

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
HELPERS = {("transcript_rows.py", "_change_row"), ("transcript_rows.py", "changing")}
MARKS = {"_touched", "_change_row", "invalidate"}
CHANGES = {"update", "pop", "setdefault", "clear", "popitem", "append", "extend", "insert", "remove", "sort",
           "reverse", "discard", "add"}
JOINS = {"append", "insert", "extend"}


def _row_list(node, aliases):
    """``<x>["messages"]``, a name bound to one, or a slice, ``reversed``/``enumerate``/``list`` of one."""
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and node.slice.value == "messages":
        return True
    if isinstance(node, ast.Name):
        return node.id in aliases
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("reversed", "enumerate", "list"):
        return bool(node.args) and _row_list(node.args[0], aliases)
    return isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice) and _row_list(node.value, aliases)


def _root(node):
    while isinstance(node, (ast.Subscript, ast.Attribute)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _called(node):
    func = node.func if isinstance(node, ast.Call) else None
    return func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None


class Function:
    """One function's rows: ``live[name]`` is the first line from which ``name`` holds a row already in a chat."""

    def __init__(self, path, node, returning, tainted_params):
        self.path, self.node, self.aliases, self.live = path, node, set(), {}
        for name in tainted_params.get(node.name, ()):
            self.live[name] = node.lineno
        for _ in range(3):   # Aliases and rows feed each other; three passes reach a fixed point in this code.
            for item in ast.walk(node):
                self._learn(item, returning)

    def _row(self, name, line):
        self.live[name] = min(line, self.live.get(name, line))

    def _learn(self, item, returning):
        if isinstance(item, ast.Assign):
            value = item.value
            for target in [t for t in item.targets if isinstance(t, ast.Name)]:
                if (isinstance(value, ast.Subscript) and isinstance(value.slice, ast.Constant)
                        and value.slice.value == "messages") or (isinstance(value, ast.Name) and value.id in self.aliases):
                    self.aliases.add(target.id)
                elif isinstance(value, ast.Subscript) and _row_list(value.value, self.aliases):
                    self._row(target.id, item.lineno)
                elif isinstance(value, ast.Call) and _called(value) in returning:
                    self._row(target.id, item.lineno)
                elif (_called(value) == "next" and value.args and isinstance(value.args[0], ast.GeneratorExp)
                      and any(_row_list(g.iter, self.aliases) for g in value.args[0].generators)):
                    self._row(target.id, item.lineno)
        elif isinstance(item, (ast.For, ast.comprehension)) and _row_list(item.iter, self.aliases):
            target = item.target.elts[-1] if isinstance(item.target, ast.Tuple) else item.target
            if isinstance(target, ast.Name):
                self._row(target.id, getattr(item, "lineno", self.node.lineno))
        elif isinstance(item, ast.Call) and _called(item) in JOINS and isinstance(item.func, ast.Attribute) \
                and _row_list(item.func.value, self.aliases):
            for argument in [a for a in item.args if isinstance(a, ast.Name)]:
                self._row(argument.id, item.lineno)
        elif isinstance(item, ast.Call) and _called(item) == "_insert_request_message":
            for argument in [a for a in item.args if isinstance(a, ast.Name)]:
                self._row(argument.id, item.lineno)

    def is_row(self, name, line):
        return name in self.live and line >= self.live[name]

    def changes(self):
        """``(node, name)`` for each change in place to a row already in a chat."""
        for item in ast.walk(self.node):
            targets = (item.targets if isinstance(item, (ast.Assign, ast.Delete))
                       else [item.target] if isinstance(item, ast.AugAssign) else [])
            for target in targets:
                if isinstance(target, ast.Subscript):
                    name = _root(target)
                    if self.is_row(name, item.lineno):
                        yield item, name
                    elif isinstance(target.value, ast.Subscript) and _row_list(target.value.value, self.aliases):
                        yield item, "<row list>[...]"
            if isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute) and item.func.attr in CHANGES:
                holder = item.func.value
                if isinstance(holder, (ast.Name, ast.Subscript, ast.Attribute)) and self.is_row(_root(holder), item.lineno) \
                        and not (isinstance(holder, ast.Name) and holder.id in self.aliases):
                    yield item, _root(holder)
                elif isinstance(holder, ast.Subscript) and not isinstance(holder.slice, ast.Slice) \
                        and _row_list(holder.value, self.aliases):
                    yield item, "<row list>[...]"

    def passed(self, function_names):
        """``(callee, position)`` for each row passed on to a function of the add-on."""
        for item in ast.walk(self.node):
            if isinstance(item, ast.Call) and _called(item) in function_names:
                for position, argument in enumerate(item.args):
                    if isinstance(argument, ast.Name) and self.is_row(argument.id, item.lineno):
                        yield _called(item), position


def _parents(tree):
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _marked(change, parents):
    """The change sits in ``with changing(...)``, or in a ``with <x>.lock:`` that marks after it or invalidates."""
    node = change
    while node in parents:
        node = parents[node]
        if not isinstance(node, ast.With):
            continue
        for item in node.items:
            expression = item.context_expr
            if isinstance(expression, ast.Call) and _called(expression) == "changing":
                return True
            if ast.unparse(expression).endswith(".lock"):
                calls = [c for c in ast.walk(node) if isinstance(c, ast.Call) and _called(c) in MARKS]
                if any(_called(c) == "invalidate" or c.lineno >= change.lineno for c in calls):
                    return True
    return False


def unmarked_row_changes(sources=None):
    """Every change in place to a chat row that is neither marked nor made before the row joins the chat."""
    texts = sources or {p.name: p.read_text(encoding="utf-8") for p in sorted(ADDON.glob("*.py"))}
    # Only a module that names a row list can hold a row (a returned one is found among these, by name, below).
    trees = {name: ast.parse(text) for name, text in texts.items() if "messages" in text}
    functions = {name: [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                 for name, tree in trees.items()}
    # A row passed on taints the parameter of a function of the same module whose name is its own there (a name
    # shared across modules, ``store`` or ``apply``, would taint unrelated code).
    local = {path: {name: [n for n in nodes if n.name == name][0] for name in {n.name for n in nodes}
                    if sum(n.name == name for n in nodes) == 1} for path, nodes in functions.items()}
    returning, tainted = set(), {}
    for _ in range(2):   # Rows returned and passed on: two rounds reach every hand-off in this code.
        for path, nodes in functions.items():
            for node in nodes:
                function = Function(path, node, returning, tainted.get(path, {}))
                for item in ast.walk(node):
                    if isinstance(item, ast.Return) and isinstance(item.value, ast.Name) \
                            and function.is_row(item.value.id, item.lineno):
                        returning.add(node.name)
                for callee, position in function.passed(local[path]):
                    parameters = [a.arg for a in local[path][callee].args.args]
                    parameters = parameters[1:] if parameters[:1] == ["self"] else parameters
                    if position < len(parameters):
                        tainted.setdefault(path, {}).setdefault(callee, set()).add(parameters[position])
    for name, text in texts.items():   # A module that only calls a function returning a row.
        if name not in trees and any(function in text for function in returning):
            trees[name] = ast.parse(text)
            functions[name] = [n for n in ast.walk(trees[name]) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    problems = []
    for path, nodes in functions.items():
        parents = _parents(trees[path])
        for node in nodes:
            if (path, node.name) in HELPERS:
                continue
            for change, name in Function(path, node, returning, tainted.get(path, {})).changes():
                if not _marked(change, parents):
                    problems.append("%s:%d %s (%s)" % (path, change.lineno, ast.unparse(change).splitlines()[0][:90], name))
    return sorted(set(problems))


class RowMarkAuditTests(unittest.TestCase):
    def test_every_change_to_a_chat_row_is_marked(self):
        self.assertEqual(unmarked_row_changes(), [])

    def test_the_audit_finds_a_kept_row_changed_after_it_joined_the_chat(self):
        """The B1 review's restore notice, in miniature: built, appended, returned, and changed by its caller."""
        source = '''
def new_notice(controller):
    notice = {"role": "system", "text": "pending"}
    notice["state"] = "pending"                       # Before it joins the chat: nothing to mark.
    with controller.lock:
        controller.state["messages"].append(notice)
    return notice

def finish(controller):
    notice = new_notice(controller)
    with controller.lock:
        notice.update(state="partial")                # 12: unmarked.
    with changing(controller, notice):
        notice.update(state="complete")               # Marked.

def badge(controller, index):
    with controller.lock:
        controller.state["messages"][index]["badge"] = True   # 18: unmarked.
    with controller.lock:
        rows = controller.state["messages"]
        rows[index].update(seen=True)
        controller._touched(index)                    # Marks the change before it, in its block.

def stamp(row):
    row["stamped"] = True                             # 25: unmarked, a row passed in.

def caller(controller):
    for row in controller.state["messages"]:
        stamp(row)
'''
        found = unmarked_row_changes({"fixture.py": source})
        self.assertEqual(sorted(int(line.split(" ")[0].split(":")[1]) for line in found), [12, 18, 25], found)

if __name__ == "__main__":
    unittest.main()
