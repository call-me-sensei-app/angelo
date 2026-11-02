# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every + menu feature the add-on's modules register, read from their source, for host tests without Blender.

A module registers its feature at its ``register`` (which needs Blender); this reads the same ``Feature(...)`` call:
its id, title and description (``n_`` text, directly or through a module constant), order, section, placement and
whether it is a group, so a host test lays out the menu people get, not a stand-in. ``chat_destinations``' own
features are registered when it is imported.
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import chat_destinations as cd  # noqa: E402


def _text(node, constants):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "n_" and isinstance(node.args[0], ast.Constant):
        return node.args[0].value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.Attribute):
        if node.attr == "bl_idname":
            return "angelo.feature"
        return getattr(cd, node.attr, None)
    return None


def _constants(tree):
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            value = _text(node.value, found)
            if isinstance(value, str):
                found[node.targets[0].id] = value
    return found


def shipped_features():
    """``chat_destinations.Feature`` for every feature a module other than ``chat_destinations`` constructs."""
    features = []
    for path in sorted(ADDON.glob("*.py")):
        if path.stem == "chat_destinations":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        constants = _constants(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) == "Feature"
                    and node.args and isinstance(node.args[0], ast.Constant)):
                continue
            values = [_text(argument, constants) for argument in node.args[:3]] + ["", ""]
            keywords = {keyword.arg: keyword.value for keyword in node.keywords}

            def get(name, default=None):
                return _text(keywords[name], constants) if name in keywords else default
            group = "children" in keywords
            features.append(cd.Feature(
                values[0], values[1] or values[0], values[2] or "", glyph=get("glyph", "dot") or "dot",
                order=get("order", 100), section=get("section", "") or "", placement=get("placement", "grid"),
                children=(lambda chosen, fit, worker=False: []) if group else None,
                operator="" if group else (get("operator") or "angelo.feature"),
                lead_only=bool(get("lead_only", False)), owner=path.stem))
    return features
