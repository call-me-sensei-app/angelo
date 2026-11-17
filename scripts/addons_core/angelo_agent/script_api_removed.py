# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Blender Python names a script may still write from an older Blender that this release removed or renamed.

``tool_specs.validate_call`` reads every reviewed script against this table before it is approved or run, and
refuses one that would certainly raise on such a name, naming the replacement. Without it the script ran until
that line raised, the host rolled the whole build back, and the next round wrote it again (doc/angelo/
agent_execution.rst, "Removed Blender API").

The rows are the certain rows of the shared table (``script_api_table``: the cited rows of
``scripts/angelo/api_drift/curated.json`` and generated rows a replay of recorded scripts cleared). Every row is a
documented removal or rename, or a recorded raise, and absent from the release's RNA
(``test_native_script_api_removed.py`` and ``test_blender_script_api_removed.py`` pin both). A use is refused only
where it certainly runs into the removed name:

* an assignment of the removed enum value as a literal (``sky.sky_type = 'NISHITA'``), never a value chosen at run
  time or compared;
* an attribute read or write of a removed name no current type has, a call of a removed operator, or a call that
  passes an operator a keyword it no longer has (``bpy.ops.object.text_add(radius=2)``), never one passed through
  ``**``;
* a listed socket key of a node type on a name the script made with ``nodes.new('<that type>')``: the Bump node's
  ``inputs['Invert']`` is an ``invert`` property (compositor nodes do have an Invert input);

and never inside a ``try`` statement, nor under an ``if``, ``while`` or conditional expression whose test asks for the
name (``hasattr``, ``in``, or the name as text): scripts that probe for either spelling keep running as before.
"""

import ast
import functools

from . import script_api_table
from .blender_release import RELEASE

BUMP = "ShaderNodeBump"  # The recorded socket case: its invert is a property (``node.invert``), not an input.
MAX_LISTED = 5


@functools.lru_cache(maxsize=1)
def _tables():
    """The refusal's lookups, from the certain rows of the shared table (``script_api_table``)."""
    values, attributes, operators, sockets, keywords = {}, {}, {}, {}, {}
    for entry in script_api_table.certain("refusal"):
        use, name = entry["use"], entry["name"]
        if use == "enum_item":
            values[(entry["property"], name)] = entry["refusal"]   # (attribute, literal value) -> the value to use.
        elif use == "attribute" and not entry.get("elsewhere"):
            attributes[name] = entry["refusal"]   # No RNA type of the release has the name.
        elif use == "operator":
            operators[name] = entry["refusal"]   # bpy.ops path -> what to call instead.
        elif use == "operator_argument":
            keywords[(entry["owner"], name)] = entry["refusal"]   # (bpy.ops path, keyword) -> the keyword to pass.
        elif use == "socket":
            sockets[(entry["owner"], entry["property"], name)] = entry["refusal"]
    return {"VALUES": values, "ATTRIBUTES": attributes, "OPERATORS": operators, "SOCKETS": sockets,
            "KEYWORDS": keywords}


def __getattr__(name):
    """``VALUES``, ``ATTRIBUTES``, ``OPERATORS``, ``SOCKETS``, ``KEYWORDS`` and ``BUMP_INVERT``, built on first use."""
    if name in ("VALUES", "ATTRIBUTES", "OPERATORS", "SOCKETS", "KEYWORDS"):
        return _tables()[name]
    if name == "BUMP_INVERT":
        return _tables()["SOCKETS"].get((BUMP, "inputs", "Invert"))
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def _dotted(node):
    """``a.b.c`` for a chain of names and attributes, else None."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        return ".".join([node.id] + parts[::-1])
    return None


def _node_kinds(tree):
    """``{name: node idname}`` for names the script binds to ``<nodes>.new('<idname>')`` (or ``type=``), for the
    node types the table lists a removed socket of."""
    listed = {owner for owner, _side, _key in _tables()["SOCKETS"]}
    kinds = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            call = node.value
            if isinstance(call.func, ast.Attribute) and call.func.attr == "new":
                given = [arg.value for arg in call.args[:1] if isinstance(arg, ast.Constant)] + [
                    keyword.value.value for keyword in call.keywords
                    if keyword.arg == "type" and isinstance(keyword.value, ast.Constant)]
                for kind in given:
                    if kind in listed:
                        kinds.update((target.id, kind) for target in node.targets if isinstance(target, ast.Name))
    return kinds


def _asks_for(test, words):
    """Whether a condition probes for one of ``words``: hasattr, ``in``, or one of them as text."""
    for node in ast.walk(test):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("hasattr", "getattr"):
            return True
        if isinstance(node, ast.Compare) and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
            return True
        if isinstance(node, ast.Constant) and node.value in words:
            return True
    return False


class _Reader(ast.NodeVisitor):
    def __init__(self, kinds):
        self.kinds, self.found, self.guards = kinds, [], []
        tables = _tables()
        self.values, self.attributes = tables["VALUES"], tables["ATTRIBUTES"]
        self.operators, self.sockets, self.keywords = tables["OPERATORS"], tables["SOCKETS"], tables["KEYWORDS"]

    def _report(self, node, what, instead):
        self.found.append((node.lineno, "line %d: %s is not in Blender %s: %s" % (node.lineno, what, RELEASE,
                                                                                   instead)))

    def _guarded(self, words):
        return any(_asks_for(test, words) for test in self.guards)

    def visit_Try(self, node):   # A script that catches a failure here decides for itself: never refused.
        return

    visit_TryStar = visit_Try

    def _branch(self, node, test, bodies):
        self.visit(test)
        self.guards.append(test)
        for body in bodies:
            for child in body if isinstance(body, list) else [body]:
                self.visit(child)
        self.guards.pop()

    def visit_If(self, node):
        self._branch(node, node.test, [node.body, node.orelse])

    visit_While = visit_If

    def visit_IfExp(self, node):
        self._branch(node, node.test, [node.body, node.orelse])

    def visit_Assign(self, node):
        value = node.value
        for target in node.targets:
            if isinstance(target, ast.Attribute) and isinstance(value, ast.Constant):
                instead = self.values.get((target.attr, value.value))
                if instead and not self._guarded({target.attr, value.value}):
                    self._report(node, "%s = %r" % (target.attr, value.value), instead)
        self.generic_visit(node)

    def visit_Attribute(self, node):
        instead = self.attributes.get(node.attr)
        if instead and not self._guarded({node.attr}):
            self._report(node, "the attribute %s" % node.attr, instead)
        self.generic_visit(node)

    def visit_Call(self, node):
        path = _dotted(node.func)
        if path and path.startswith("bpy.ops."):
            operator = path[len("bpy.ops."):]
            if operator in self.operators and not self._guarded({path.rsplit(".", 1)[-1]}):
                self._report(node, path, self.operators[operator])
            for keyword in node.keywords:   # ``**options`` has no name (arg None): never refused.
                instead = self.keywords.get((operator, keyword.arg))
                if instead and not self._guarded({keyword.arg}):
                    self._report(node, "%s(%s=...)" % (path, keyword.arg), instead)
        self.generic_visit(node)

    def visit_Subscript(self, node):
        holder = node.value
        if (isinstance(holder, ast.Attribute) and holder.attr in ("inputs", "outputs")
                and isinstance(holder.value, ast.Name) and holder.value.id in self.kinds
                and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str)):
            instead = self.sockets.get((self.kinds[holder.value.id], holder.attr, node.slice.value))
            if instead and not self._guarded({node.slice.value}):
                self._report(node, "%s.%s[%r]" % (holder.value.id, holder.attr, node.slice.value), instead)
        self.generic_visit(node)


def problems(code):
    """The lines of ``code`` that certainly use a name this release removed, each with its replacement, in order (at
    most MAX_LISTED); [] when there are none or the code does not parse (its syntax error is reported elsewhere)."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return []
    reader = _Reader(_node_kinds(tree))
    reader.visit(tree)
    return [text for _line, text in sorted(set(reader.found))][:MAX_LISTED]


def refusal(found):
    """The validation error for a script with removed names: nothing has run."""
    return ("This script uses Blender Python that Blender %s removed, so it was not run: " % RELEASE + "; ".join(found)
            + ". Correct those lines and send the script again.")
