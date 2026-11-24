# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The interface text a registered class carries, read from the Python sources (standard library only, no ``bpy``).

Blender translates a class's label, tooltip and property text by itself, from RNA, so nothing marks them and the
bypass scanner rightly accepts them (``scan.NATIVE_CLASS_ATTRIBUTES``). They still need an entry in the template,
or they show in English in every language. Two things made that entry depend on luck:

* **The app's RNA dump never sees a Python class's tooltip.** The struct Blender registers for a Python operator,
  menu, panel or node carries no description, so ``_bl_i18n_utils`` finds none for ``bl_description``. On 2026-11-12
  103 add-on classes set one; a full extraction on the 1.1 build emitted 4 of them.
* **Where a class sets no ``bl_description``, Blender registers its docstring in its place** (``bpy_rna.cc``,
  ``bpy_property_substitutions``), and the dump takes the docstring as interface text. 41 add-on classes relied on
  that; 9 of those docstrings were notes for developers, shown as tooltips and translated into 20 languages.

So the class text is read here, from the sources, for the extraction (``extract_pot``) and for the host suite
(``test_native_translation_class_text``): every such text is in the template, and no registered class leaves its
tooltip to its docstring (a real ``bl_description``, or ``""`` for none).
"""

import ast

LABEL, DESCRIPTION, CATEGORY = "bl_label", "bl_description", "bl_category"
ATTRIBUTES = (LABEL, DESCRIPTION, CATEGORY)
PROPERTY_NAME, PROPERTY_DESCRIPTION, ITEM_NAME, ITEM_DESCRIPTION = (
    "property name", "property description", "enum item name", "enum item description")
_REGISTERS = ("bl_idname", LABEL)   # A class Blender registers assigns one of these in its body.


def _literal(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _assigned(node):
    """``(name, value node)`` of a plain ``name = value`` statement, else ``(None, None)``."""
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        return node.targets[0].id, node.value
    return None, None


def registered(node):
    """Whether ``node`` is a class Blender registers (an operator, menu, panel, node, node tree...)."""
    return isinstance(node, ast.ClassDef) and any(_assigned(statement)[0] in _REGISTERS for statement in node.body)


def _property_text(call):
    """``[(kind, text, line)]`` of a ``...Property(...)`` call: its name, its description and its enum items'."""
    found = []
    for keyword in call.keywords:
        if keyword.arg == "name" and _literal(keyword.value):
            found.append((PROPERTY_NAME, _literal(keyword.value), keyword.value.lineno))
        elif keyword.arg == "description" and _literal(keyword.value):
            found.append((PROPERTY_DESCRIPTION, _literal(keyword.value), keyword.value.lineno))
        elif keyword.arg == "items" and isinstance(keyword.value, (ast.List, ast.Tuple)):
            for item in keyword.value.elts:
                if isinstance(item, (ast.Tuple, ast.List)) and len(item.elts) >= 3:
                    for part, kind in ((item.elts[1], ITEM_NAME), (item.elts[2], ITEM_DESCRIPTION)):
                        if _literal(part):
                            found.append((kind, _literal(part), part.lineno))
    return found


def messages(source):
    """``[(kind, text, owner, line)]``: the label, tooltip and tab of every registered class in ``source`` and the
    names, descriptions and enum items of every property it defines (``owner``: the class, or ``""`` for a property
    defined outside one). Literals only: text built at runtime goes through ``ui_text`` and is the scanner's."""
    found = []
    tree = ast.parse(source)
    owners = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for inner in ast.walk(node):
                owners.setdefault(id(inner), node.name)
            if registered(node):
                for statement in node.body:
                    name, value = _assigned(statement)
                    if name in ATTRIBUTES and _literal(value):
                        found.append((name, _literal(value), node.name, value.lineno))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if name.endswith("Property"):
                found += [(kind, text, owners.get(id(node), ""), line) for kind, text, line in _property_text(node)]
    return found


def docstring_tooltips(source):
    """``[(class, line)]`` of the registered classes in ``source`` that set no ``bl_description`` and have a
    docstring: Blender registers that docstring as the class's tooltip."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if registered(node) and ast.get_docstring(node, clean=False) and not any(
                _assigned(statement)[0] == DESCRIPTION for statement in node.body):
            found.append((node.name, node.lineno))
    return found


def docstrings(source):
    """Every class docstring in ``source``, as written and with its indentation removed (the form the app's RNA dump
    writes for a docstring over several lines): what the dump may have taken for interface text."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ClassDef) and ast.get_docstring(node, clean=False):
            found |= {ast.get_docstring(node, clean=False), ast.get_docstring(node, clean=True)}
    return found


def tooltips(sources):
    """``{text: {origin}}`` of every ``bl_description`` in ``sources`` (``{origin: source text}``): the class text
    the app's RNA dump cannot see, which the extraction therefore takes from here."""
    found = {}
    for origin, source in sources.items():
        for kind, text, _owner, _line in messages(source):
            if kind == DESCRIPTION:
                found.setdefault(text, set()).add(origin)
    return found


def dump_artifact(msgid, origins, class_docstrings, class_tooltips):
    """Whether the app's RNA dump made ``msgid`` up from a class docstring: every origin is a class
    (``bpy.types...``), the text is a docstring of the add-on and no class states it as its tooltip."""
    return (bool(origins) and all(origin.startswith("bpy.types.") for origin in origins)
            and msgid in class_docstrings and msgid not in class_tooltips)
