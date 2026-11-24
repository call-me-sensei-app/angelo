# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Blender's native LoopTools operators sit together in one Loops menu, under the names LoopTools users search for.
No Blender.

Blender 5.2 and 5.3 made the LoopTools add-on's tools native (To Circle, Flatten, Space, Relax, Curve, and Gstretch as
"Fit Edge Loops To Annotation"), but drew them unlabelled as a group inside Mesh › Transform, under long names, and
outside the edit-mode context menu. A LoopTools user searching F3 for "Relax" or "Gstretch" finds an unfamiliar label
or nothing, and the launch thread for Blender 5.3 asked for the integration to be rethought (the Blender 5.3 launch
feedback notes of 2026-11-14, item 9; the 2.1.0 release plan, section 4.2).

Covered, read from the sources (the add-on ``angelo_agent`` and ``bl_ui/space_view3d.py``, so the menu may live in
either) and the operators' C sources:

* exactly one menu titled "Loops" exists, and it offers every native operator LoopTools users know, each with text
  containing the LoopTools name (Bridge, Circle, Curve, Flatten, Gstretch, Relax, Space), so search finds it by that
  name. LoopTools' Loft has no native operator and is not required;
* the Edge menu, a header menu that F3 search walks (``interface_template_search_menu.cc``), shows the Loops menu
  under its own title, so search shows Edge › Loops › Relax;
* the edit-mode context menu shows the Loops menu too (right-click, where LoopTools put its menu);
* every operator named here exists in ``source/blender/editors/mesh``, so a rename upstream fails this test at the
  next base move instead of leaving a dead menu entry.

``test_blender_loops_menu`` proves the same in the running app: the menu opens from both places, F3 finds its items,
and each item runs on a real loop selection in Director Mode.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCES = sorted((ROOT / "scripts/addons_core/angelo_agent").glob("*.py")) + [
    ROOT / "scripts/startup/bl_ui/space_view3d.py"]
MESH_EDITORS = ROOT / "source/blender/editors/mesh"

# LoopTools' own menu names and the native operator that does the same job (Blender 5.2/5.3).
LOOPTOOLS = {
    "Bridge": "mesh.bridge_edge_loops",
    "Circle": "mesh.circularize",
    "Curve": "mesh.curve_edge_loops_between_selected",
    "Flatten": "mesh.flatten",
    "Gstretch": "mesh.fit_edge_loops_to_annotation",
    "Relax": "mesh.relax_edge_loops",
    "Space": "mesh.space_edge_loops_evenly",
}
EDGE_MENU = "VIEW3D_MT_edit_mesh_edges"
CONTEXT_MENU = "VIEW3D_MT_edit_mesh_context_menu"


def text_of(node):
    """A string literal, or the literal inside a translation marker (``n_("x")``, ``iface_("x")``); else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Call) and node.args and getattr(node.func, "id", None) in ("n_", "iface_", "tip_"):
        return text_of(node.args[0])
    return None


def parsed():
    return [(path, ast.parse(path.read_text(encoding="utf-8"))) for path in SOURCES]


def menu_classes(trees):
    """Every Menu subclass: [(path, tree, ClassDef, bl_label, bl_idname)]."""
    found = []
    for path, tree in trees:
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if not any(getattr(base, "id", getattr(base, "attr", None)) == "Menu" for base in node.bases):
                continue
            values = {}
            for item in node.body:
                if isinstance(item, ast.Assign):
                    for target in item.targets:
                        if getattr(target, "id", None) in ("bl_label", "bl_idname"):
                            values[target.id] = text_of(item.value)
            found.append((path, tree, node, values.get("bl_label"), values.get("bl_idname") or node.name))
    return found


def offered(tree, classdef):
    """(operator idname, text) pairs the menu offers: direct ``operator("x", text=...)`` calls in its draw, and
    (idname, text) pairs in tables of the same module (a draw that loops over a table)."""
    pairs = []
    for node in ast.walk(classdef):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "operator" and node.args:
            idname = text_of(node.args[0])
            text = next((text_of(k.value) for k in node.keywords if k.arg == "text"), None)
            if idname:
                pairs.append((idname, text))
    for node in ast.walk(tree):
        if isinstance(node, ast.Tuple) and len(node.elts) in (2, 3):   # (operator, text[, its context])
            idname, text = text_of(node.elts[0]), text_of(node.elts[1])
            if idname and idname.startswith("mesh.") and text:
                pairs.append((idname, text))
    return pairs


def shows_menu(trees, host, idname):
    """Whether Blender's menu ``host`` shows the menu ``idname`` under its own title: in the host's own draw, or in a
    function appended or prepended to it whose body draws ``layout.menu(idname)`` (or ``Class.bl_idname``) with no
    ``text=``."""
    def draws(function_node):
        for node in ast.walk(function_node):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "menu" and node.args:
                arg = node.args[0]
                named = text_of(arg) == idname or (
                    isinstance(arg, ast.Attribute) and arg.attr == "bl_idname" and getattr(arg.value, "id", None) == idname)
                if named and not any(k.arg == "text" for k in node.keywords):
                    return True
        return False

    for _, tree in trees:
        functions = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == host:
                if any(draws(item) for item in node.body if isinstance(item, ast.FunctionDef) and item.name == "draw"):
                    return True
            if (isinstance(node, ast.Call) and getattr(node.func, "attr", None) in ("append", "prepend")
                    and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == host and node.args
                    and isinstance(node.args[0], ast.Name) and node.args[0].id in functions):
                if draws(functions[node.args[0].id]):
                    return True
    return False


def operator_idnames():
    """Every operator idname the mesh editors register (``ot->idname = "MESH_OT_x"`` -> ``mesh.x``)."""
    names = set()
    for path in MESH_EDITORS.glob("*.cc"):
        for match in re.finditer(r'ot->idname\s*=\s*"MESH_OT_(\w+)"', path.read_text(encoding="utf-8")):
            names.add("mesh." + match.group(1))
    return names


class LoopsMenu(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.trees = parsed()
        cls.loops = [entry for entry in menu_classes(cls.trees) if entry[3] == "Loops"]

    def the_menu(self):
        self.assertEqual(len(self.loops), 1, "exactly one menu titled 'Loops': %s"
                         % [(str(p.relative_to(ROOT)), c.name) for p, _, c, _, _ in self.loops])
        return self.loops[0]

    def test_every_looptools_job_under_its_name(self):
        _, tree, classdef, _, _ = self.the_menu()
        pairs = offered(tree, classdef)
        for word, idname in LOOPTOOLS.items():
            with self.subTest(looptools=word):
                texts = [text for op, text in pairs if op == idname]
                self.assertTrue(texts, "%s (LoopTools' %s) is not in the Loops menu" % (idname, word))
                self.assertTrue(any(t and word.lower() in t.lower() for t in texts),
                                "%s's menu text %r does not contain LoopTools' name %r, so search by it misses"
                                % (idname, texts, word))

    def test_edge_menu_shows_loops_under_its_own_title(self):
        _, _, classdef, _, idname = self.the_menu()
        self.assertTrue(shows_menu(self.trees, EDGE_MENU, idname) or shows_menu(self.trees, EDGE_MENU, classdef.name),
                        "the Edge header menu does not show the Loops menu under its own title, so F3 cannot reach it")

    def test_context_menu_shows_loops(self):
        _, _, classdef, _, idname = self.the_menu()
        self.assertTrue(shows_menu(self.trees, CONTEXT_MENU, idname) or shows_menu(self.trees, CONTEXT_MENU, classdef.name),
                        "the edit-mode context menu does not show the Loops menu")

    def test_every_named_operator_exists(self):
        registered = operator_idnames()
        self.assertGreater(len(registered), 100, "read too few mesh operators to prove anything")
        missing = sorted(set(LOOPTOOLS.values()) - registered)
        self.assertEqual(missing, [], "operators renamed or removed upstream: %s" % missing)


if __name__ == "__main__":
    unittest.main()
