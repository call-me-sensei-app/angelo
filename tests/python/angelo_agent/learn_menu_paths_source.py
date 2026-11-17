# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Builds ``learn_menu_paths.PATHS`` from Blender's own menu sources (``scripts/startup/bl_ui``). No Blender.

A how-to card for an operator Angelo ran by name (``operator_run``) names the operator, and Blender's operator search
(F3) finds a command only through the menus of the editor's header (``interface_template_search_menu.cc`` walks the
editor's ``*_MT_editor_menus`` down and leaves context menus out). So a card must show where the header holds the
command, as the menus write it, or nothing at all: the operator's own label is often not the menu's text ("Multi
Select Edge Loops" is Select › Select Loops › Edge Loops), and a learner who searched for the label found nothing.

``build()`` walks, for every allow-listed operator, the header menus its editor and mode show
(``VIEW3D_MT_editor_menus``, ``IMAGE_MT_editor_menus``) down through ``layout.menu`` (a breadcrumb, only when the
parent shows the child under the child's own title) and ``layout.menu_contents`` (the child's items drawn in place),
and keeps every plain ``operator`` entry with the properties the menu sets on it and the menu's own ``text=``.
An entry whose text or properties are worked out at run time is left out: its words cannot be known here.

    python3 tests/python/angelo_agent/learn_menu_paths_source.py --write   # Regenerate after a bl_ui change.

``test_native_learn_menu_paths`` fails while the checked-in table differs from ``build()``.
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BL_UI = ROOT / "scripts/startup/bl_ui"
TABLE = ROOT / "scripts/addons_core/angelo_agent/learn_menu_paths.py"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import action_operator_spec, learn_routes  # noqa: E402
from angelo_agent.learn_routes import EDIT, OBJECT, POSE, UV_EDITOR, VIEW_3D  # noqa: E402

_DYNAMIC = object()   # A value the source works out at run time.
# The object type an Edit Mode operator works on, by its module ("VIEW3D_MT_edit_" + the type, as the header names it).
EDIT_TYPES = {"mesh": "mesh", "transform": "mesh", "curve": "curve", "curves": "curves", "font": "text",
              "armature": "armature", "lattice": "lattice", "grease_pencil": "grease_pencil"}
# The header's menu for an Edit Mode type when it is not "VIEW3D_MT_edit_" + the type (edit_object.type.lower()).
_EDIT_MENU = {"text": "VIEW3D_MT_edit_font", "grease_pencil": "VIEW3D_MT_edit_greasepencil"}
# The extra menus the header draws in Edit Mode for a type (VIEW3D_MT_editor_menus.draw).
_EDIT_EXTRA = {"mesh": ("VIEW3D_MT_edit_mesh_vertices", "VIEW3D_MT_edit_mesh_edges", "VIEW3D_MT_edit_mesh_faces"),
               "curve": ("VIEW3D_MT_edit_curve_ctrlpoints", "VIEW3D_MT_edit_curve_segments"),
               "curves": ("VIEW3D_MT_edit_curves_control_points", "VIEW3D_MT_edit_curves_segments"),
               "grease_pencil": ("VIEW3D_MT_edit_greasepencil_point", "VIEW3D_MT_edit_greasepencil_stroke")}


def roots(editor, mode, operator):
    """The header menus shown under their own title in ``editor`` and ``mode`` (the header's Add menu in Edit Mode
    and the 3D view's UV menu are drawn under another text, so a breadcrumb could not name them)."""
    if editor == UV_EDITOR:
        return ("IMAGE_MT_view", "IMAGE_MT_select", "IMAGE_MT_uvs")
    if mode == OBJECT:
        return ("VIEW3D_MT_view", "VIEW3D_MT_select_object", "VIEW3D_MT_add", "VIEW3D_MT_object")
    if mode == POSE:
        return ("VIEW3D_MT_view", "VIEW3D_MT_select_pose", "VIEW3D_MT_pose")
    kind = EDIT_TYPES.get(operator.split(".", 1)[0])
    if kind is None:
        return ()
    return (("VIEW3D_MT_view", "VIEW3D_MT_select_edit_" + kind, _EDIT_MENU.get(kind, "VIEW3D_MT_edit_" + kind))
            + _EDIT_EXTRA.get(kind, ()))


def _constant(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, float, bool)):
        return node.value
    if isinstance(node, (ast.Tuple, ast.List)):
        values = tuple(_constant(item) for item in node.elts)
        return _DYNAMIC if _DYNAMIC in values else values
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        value = _constant(node.operand)
        return -value if isinstance(value, (int, float)) and not isinstance(value, bool) else _DYNAMIC
    return _DYNAMIC


def _call(node, name):
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == name
            and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str))


def _keyword(call, name, default=None):
    return next((_constant(keyword.value) for keyword in call.keywords if keyword.arg == name), default)


class _Menu:
    def __init__(self):
        self.label, self.bases, self.edges, self.entries = "", set(), [], []


def _entry(menu, call):
    """An ``operator`` call's entry: [idname, text, {property: value}]; None when its text is worked out at run time
    or translated in a context of its own."""
    text = _keyword(call, "text", "")
    if text is _DYNAMIC or _keyword(call, "text_ctxt") is not None or _keyword(call, "translate", True) is not True:
        found = None
    else:
        found = [call.args[0].value, text, {}]
    menu.entries.append(found)
    return found


_BLOCKS = (ast.If, ast.For, ast.While, ast.With, ast.Try)


def _calls(menu, node, skip=None):
    """The entries, submenus and bases an expression or simple statement draws."""
    for found in ast.walk(node):
        if found is skip:
            continue
        if _call(found, "operator"):
            _entry(menu, found)
        elif _call(found, "menu"):
            menu.edges.append(("menu", found.args[0].value, _keyword(found, "text")))
        elif _call(found, "menu_contents"):
            menu.edges.append(("inline", found.args[0].value, None))
        elif isinstance(found, ast.Attribute) and found.attr == "draw" and isinstance(found.value, ast.Name):
            menu.bases.add(found.value.id)   # ``Base.draw(self, context)``: the base's items are drawn here too.


def _set(names, owner, name, value):
    """A property set on the entry (or entries) a name holds; one that could be either of several branches' entries
    is dropped (``_DYNAMIC``), since which value goes with which entry is not known here."""
    found = names.get(owner)
    if isinstance(found, list):
        found[2][name] = value
    elif isinstance(found, tuple):
        for entry in found:
            if entry is not None:
                entry[2][name] = _DYNAMIC


def _statements(menu, body, names):
    """Read a block in order: the entries it draws and the properties set on them. ``names`` maps a local name to
    the entry it holds (a list), to the entries it may hold after branches (a tuple), or to None."""
    for statement in body:
        value = getattr(statement, "value", None)
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
            if isinstance(target, ast.Name):
                names.pop(target.id, None)
                if _call(value, "operator"):
                    names[target.id] = _entry(menu, value)
                    _calls(menu, value, skip=value)   # Anything drawn in the call's arguments.
                    continue
            elif isinstance(target, ast.Attribute) and _call(target.value, "operator"):
                found = _entry(menu, target.value)
                if found is not None:
                    found[2][target.attr] = _constant(value)
                continue
            elif isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id in names:
                _set(names, target.value.id, target.attr, _constant(value))
                continue
        if isinstance(statement, _BLOCKS):
            for field in ("test", "iter", "items", "subject"):
                part = getattr(statement, field, None)
                if part is not None:
                    _calls(menu, part) if isinstance(part, ast.AST) else [_calls(menu, item) for item in part]
            assigned = {}
            for field in ("body", "orelse", "finalbody", "handlers"):
                for block in _blocks(getattr(statement, field, None)):
                    inner = dict(names)
                    _statements(menu, block, inner)
                    for name, held in inner.items():
                        if held is not names.get(name):
                            assigned.setdefault(name, []).extend(held if isinstance(held, tuple) else [held])
            for name, held in assigned.items():   # After the branches: whichever of them ran.
                names[name] = tuple(held) + ((names[name],) if isinstance(names.get(name), list) else ())
            continue
        if isinstance(statement, (ast.FunctionDef, ast.ClassDef)):
            _statements(menu, statement.body, {})
            continue
        _calls(menu, statement)


def _blocks(field):
    if not isinstance(field, list):
        return []
    if field and isinstance(field[0], ast.ExceptHandler):
        return [handler.body for handler in field]
    return [field]


def menus():
    """{menu class: _Menu} of every ``*_MT_*`` class in ``bl_ui``."""
    found = {}
    for path in sorted(BL_UI.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ClassDef) or "_MT_" not in node.name:
                continue
            menu = found.setdefault(node.name, _Menu())
            menu.bases |= {base.id for base in node.bases if isinstance(base, ast.Name)}
            for item in node.body:
                if (isinstance(item, ast.Assign) and isinstance(item.value, ast.Constant)
                        and any(getattr(target, "id", "") == "bl_label" for target in item.targets)):
                    menu.label = item.value.value
                elif isinstance(item, ast.FunctionDef):
                    _statements(menu, item.body, {})
    return found


def _label(found, name, seen=()):
    menu = found.get(name)
    if menu is None or name in seen:
        return ""
    return menu.label or next((text for text in (_label(found, base, seen + (name,)) for base in sorted(menu.bases))
                               if text), "")


def _drawn(found, name, field, seen=()):
    """What ``name`` draws of ``field`` ("edges" or "entries"), its bases' and the menus it draws in place included."""
    menu = found.get(name)
    if menu is None or name in seen:
        return []
    drawn = list(getattr(menu, field))
    for base in sorted(menu.bases):
        drawn += _drawn(found, base, field, seen + (name,))
    for kind, child, _ in menu.edges:
        if kind == "inline":
            drawn += _drawn(found, child, field, seen + (name,))
    return drawn


def chains(found, start):
    """{menu: the shortest breadcrumb chain from the header menus ``start`` to it}, each crumb the menu's own title."""
    reached = {name: (name,) for name in start if _label(found, name)}
    queue = list(reached)
    while queue:
        name = queue.pop(0)
        for kind, child, text in _drawn(found, name, "edges"):
            title = _label(found, child)
            if kind != "menu" or child in reached or not title or text not in (None, title):
                continue
            if child.endswith("_context_menu"):   # Search leaves context menus out.
                continue
            reached[child] = reached[name] + (child,)
            queue.append(child)
    return reached


def build():
    """{operator: ((menus, item, ((property, value), ...)), ...)}, shortest path first. The editor and mode are the
    operator's route's (``learn_routes``), so the table does not repeat them."""
    found = menus()
    table = {}
    for operator in sorted(action_operator_spec.ALLOWED):
        (route,) = learn_routes.hand({"kind": "operator_run", "parameters": {"operator": operator}}).routes
        entries = set()
        for menu, chain in chains(found, roots(route.editor, route.mode, operator)).items():
            for entry in _drawn(found, menu, "entries"):
                if entry is None or entry[0] != operator or _DYNAMIC in entry[2].values():
                    continue
                entries.add((chain, entry[1], tuple(sorted(entry[2].items()))))
        if entries:
            table[operator] = tuple(sorted(entries, key=lambda item: (len(item[0]), item)))
    return table


HEADER = '''# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where each allow-listed operator sits in its editor's header menus (learn mode). GENERATED: do not edit.

Regenerate with ``python3 tests/python/angelo_agent/learn_menu_paths_source.py --write`` after Blender's menu sources
change; ``test_native_learn_menu_paths`` fails until then. Built from ``scripts/startup/bl_ui`` only.

``PATHS[idname]`` holds ``(menus, item, properties)`` entries, shortest first, in the header menus the operator's
route's editor and mode show: the menu classes from the header down, the menu's own text for the command ("" when it
shows the operator's label; Blender's message, which its catalogue translates) and the properties the entry sets.
Identifiers and Blender's own text only; no model reads it.
"""

PATHS = {
'''


def render(table):
    lines = []
    for operator, entries in table.items():
        line = "    %r: (%s)," % (operator, " ".join(repr(entry) + "," for entry in entries))
        if len(line) <= 120:
            lines.append(line)
            continue
        lines.append("    %r: (" % operator)
        for entry in entries:
            line = "        %r," % (entry,)
            lines += [line] if len(line) <= 120 else ["        (%r," % (entry[0],), "         %r, %r)," % entry[1:]]
        lines.append("    ),")
    return HEADER + "\n".join(lines) + "\n}\n"


if __name__ == "__main__":
    text = render(build())
    if "--write" in sys.argv:
        TABLE.write_text(text, encoding="utf-8")
        print("wrote %s (%d operators)" % (TABLE.relative_to(ROOT), text.count("\n    '")))
    else:
        sys.stdout.write(text)
