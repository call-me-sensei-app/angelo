# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Blender 4.x names a script still uses that Blender 5.2 renamed or removed: a note in its result, never a refusal.

Set inside try/except, a removed name fails silently and the script's fallback runs: a ``sky_type =
'NISHITA'`` left a flat grey world and the reply claimed a physical sky. After a script runs,
its result carries ``api_notes``: one line per listed name its code uses, with the 5.2 name. The line is
there whether the use raised or was caught; the model decides what to do with it.

``RENAMED`` is the whole list, small on purpose, and data: each row names how the code uses the old name
(``string``: an exact string constant; ``attribute``: an attribute, or getattr/setattr/hasattr's name), the
5.2 owner it is checked against and its 5.2 replacements: every one, where 5.2 has two ('NISHITA' was
renamed 'SINGLE_SCATTERING', which old files load as, and a new Sky Texture defaults to
'MULTIPLE_SCATTERING'). Only names whose use is unambiguous are listed: an attribute that some other 5.2
type still has (``fcurves`` on a channelbag) is listed with ``on``, the kind of value it must be read from,
and is matched only there. Each row is checked against Blender 5.2's own Python API by
``test_blender_script_api_notes.py``; the recorded runs had these four as the version-drift names scripts
used (``qa/MISTAKE-CATALOGUE.md``, class 6). Each note names the same 5.2 names as the error Angelo's
Blender adds for that name (``bpy_angelo_renamed_api.cc``), so whatever follows a note's ": use " must
appear in that error's sentence; ``test_blender_renamed_api_errors.py`` checks it where both are present.

The scan reads the code's syntax tree (never comments) and parses only when a listed name occurs in the text.
"""

import ast

RENAMED = (
    # Two 5.2 values: NISHITA's own model, renamed (old files load as it), and the newer default.
    {"string": "NISHITA", "owner": "ShaderNodeTexSky.sky_type",
     "note": "Blender 5.2 has no sky_type 'NISHITA': use 'SINGLE_SCATTERING' (the same model: files saved with "
             "'NISHITA' open with it); the new default is 'MULTIPLE_SCATTERING'."},
    {"string": "BLENDER_EEVEE_NEXT", "owner": "RenderSettings.engine",
     "note": "Blender 5.2 has no render engine 'BLENDER_EEVEE_NEXT': use 'BLENDER_EEVEE'."},
    {"attribute": "dust_density", "owner": "ShaderNodeTexSky",
     "note": "Blender 5.2's Sky Texture has no dust_density: use aerosol_density."},
    {"attribute": "fcurves", "on": "action", "owner": "Action",
     "note": "Blender 5.2's Action has no fcurves: use "
             "bpy_extras.anim_utils.action_get_channelbag_for_slot(action, animation_data.action_slot).fcurves."},
)
CAUGHT = "Inside try/except such a use fails silently and the fallback runs: check what the script actually set."
_NAMED = frozenset({"getattr", "setattr", "hasattr", "delattr"})  # Their second argument is the name.


def _scan(tree):
    """One pass over the tree: string constants, (attribute, receiver) uses and the names bound to an Action.

    Names bound to an Action: ``a = <x>.action`` (or an item of ``.actions``) and ``for a in <x>.actions``.
    """
    strings, uses, actions = set(), [], set()
    attributes = {row["attribute"] for row in RENAMED if "attribute" in row}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            strings.add(node.value)
        elif isinstance(node, ast.Attribute) and node.attr in attributes:
            uses.append((node.attr, node.value))
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _NAMED
              and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and node.args[1].value in attributes):
            uses.append((node.args[1].value, node.args[0]))
        if isinstance(node, ast.Assign) and _is_action(node.value, actions):
            actions.update(target.id for target in node.targets if isinstance(target, ast.Name))
        elif (isinstance(node, (ast.For, ast.comprehension)) and _is_actions(node.iter)
              and isinstance(node.target, ast.Name)):
            actions.add(node.target.id)
    return strings, uses, actions


def _is_actions(node):
    """``bpy.data.actions`` (or any ``.actions`` collection)."""
    return isinstance(node, ast.Attribute) and node.attr == "actions"


def _is_action(node, names):
    """Whether an expression is an Action: ``<x>.action``, a name bound to one, or an item of ``.actions``."""
    if isinstance(node, ast.Attribute):
        return node.attr == "action"
    if isinstance(node, ast.Name):
        return node.id in names
    if isinstance(node, ast.Subscript):
        return _is_actions(node.value)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return node.func.attr in ("get", "new") and _is_actions(node.func.value)
    return False


def _uses(row, strings, uses, actions):
    if "string" in row:
        return row["string"] in strings
    if "on" not in row and row["attribute"] in strings:
        return True  # No 5.2 type has it: the name as data (``setattr(sky, name, value)`` in a loop) is a use too.
    return any(name == row["attribute"] and ("on" not in row or _is_action(receiver, actions))
               for name, receiver in uses)


def notes(code):
    """``{"api_notes": [...]}`` for the listed names ``code`` uses, or ``{}``."""
    if not isinstance(code, str) or not any(row.get("string", row.get("attribute")) in code for row in RENAMED):
        return {}
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError):
        return {}
    scanned = _scan(tree)
    found = [row["note"] for row in RENAMED if _uses(row, *scanned)]
    return {"api_notes": found + [CAUGHT]} if found else {}
