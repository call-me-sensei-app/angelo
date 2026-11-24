# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The GUI suites turn a surface's coordinates into window pixels only through ``qa_hit`` (2.1.0).

A suite that added a hit rect to its region's origin by hand clicked where the button would be at interface scale 1,
left to right: beside it at any other scale (an Xvfb display at a DPI other than 96, a Retina screen, Resolution
Scale) or in a right-to-left language. On 2026-11-15 a suite under a 100 DPI display (ui_scale 1.0417) missed the +
menu's Render row that way, and a check at 1.0, 1.5 and 2.0 showed every such hand mapping drifting by the scale.

``test_no_suite_maps_a_surface_point_by_hand`` refuses the raw form in every file of the suites: an arithmetic
expression that adds a region's origin (``region.x``, ``region.y``; any name bound from a region) to anything but the
region's own size and literals. ``qa_hit.xy``/``point``/``rect`` take the surface whose space the coordinates are in.
Bounds checks (an origin inside a comparison) are not mappings and are left alone. The other tests pin ``qa_hit``'s
arithmetic and that each surface's input still maps the pointer as ``qa_hit`` inverts it.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(HERE))

import qa_hit  # noqa: E402

EXEMPT = {"qa_hit.py"}   # The one place the origin is added.
PURE = {"int", "round", "float", "abs"}


REGION_CALL = re.compile(r"(?:^|_)region(?:_of)?$")


def _is_region_value(value):
    """An expression whose value is a region: one taken from ``.regions``, or a call to a function named for one
    (``region()``, ``hero_region(area)``, ``region_of(area)``). A projection (``location_3d_to_region_2d``) is not."""
    if isinstance(value, ast.Call):
        func = value.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
        if REGION_CALL.search(name):
            return True
    return any(isinstance(node, ast.Attribute) and node.attr == "regions" for node in ast.walk(value))


def _region_names(tree):
    """Single names bound to a region (``r = next(r for r in area.regions ...)``, ``r = region()``)."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            pairs = [(target, node.value) for target in node.targets]
        elif isinstance(node, (ast.For, ast.comprehension)):
            pairs = [(node.target, node.iter)]
        elif isinstance(node, ast.NamedExpr):
            pairs = [(node.target, node.value)]
        else:
            continue
        for target, value in pairs:
            if isinstance(target, ast.Name) and _is_region_value(value):
                names.add(target.id)
    return names


def raw_mappings(source):
    """(line, expression) of each hand mapping in ``source``: an origin plus anything but the region's size."""
    tree = ast.parse(source)
    regions = _region_names(tree)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}

    def regionish(base):
        text = ast.unparse(base)
        return "region" in text.lower() or text in regions

    def attribute(node, names):
        return isinstance(node, ast.Attribute) and node.attr in names and regionish(node.value)

    def pure(node):
        return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in PURE \
            and len(node.args) == 1 and not node.keywords

    def leaves(node):
        if isinstance(node, ast.BinOp):
            return leaves(node.left) + leaves(node.right)
        if isinstance(node, ast.UnaryOp):
            return leaves(node.operand)
        if pure(node):
            return leaves(node.args[0])
        return [node]

    found = set()
    for node in ast.walk(tree):
        if not attribute(node, ("x", "y")):
            continue
        top = node
        while top in parents and (isinstance(parents[top], (ast.BinOp, ast.UnaryOp)) or pure(parents[top])):
            top = parents[top]
        if top is node or isinstance(parents.get(top), ast.Compare):
            continue
        if any(not (attribute(leaf, ("x", "y", "width", "height")) or isinstance(leaf, ast.Constant))
               for leaf in leaves(top)):
            found.add((top.lineno, ast.unparse(top)))
    return sorted(found)


class SuitesMapThroughQaHit(unittest.TestCase):
    def test_every_file_that_maps_through_qa_hit_imports_it(self):
        """A suite that calls ``qa_hit.<name>`` imports ``qa_hit`` itself: a merge that kept another side's import
        block left three Studio suites with a NameError at their first click (2.1.0 rebase, 2026-11-18)."""
        missing = []
        for path in sorted(HERE.rglob("*.py")):
            if path.name in EXEMPT:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            uses = any(isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "qa_hit"
                       for n in ast.walk(tree))
            imports = any(isinstance(n, ast.Import) and any(a.name == "qa_hit" and a.asname is None for a in n.names)
                          for n in ast.walk(tree))
            if uses and not imports:
                missing.append(path.name)
        self.assertEqual(missing, [], "import qa_hit where it is used")

    def test_no_suite_maps_a_surface_point_by_hand(self):
        offenders = []
        for path in sorted(HERE.rglob("*.py")):
            if path.name in EXEMPT:
                continue
            for line, text in raw_mappings(path.read_text(encoding="utf-8")):
                offenders.append("%s:%d: %s" % (path.relative_to(HERE), line, text[:120]))
        self.assertEqual(offenders, [], "map with qa_hit.xy/point/rect and the surface's space, never by hand "
                                        "(%d sites)" % len(offenders))

    def test_the_scan_finds_each_raw_form(self):
        cases = {
            "x = region.x + rect[0] + rect[2] / 2": 1,
            "x, y = region.x + x + w / 2, region.y + y + h / 2": 2,
            "r = next(r for r in area.regions if r.type == 'WINDOW')\np = (r.x + x, r.y + y)": 2,
            "x = int((region.x + rect[0]) * k)": 1,
            "x = region.x + region.width - int(rect[0] + rect[2] / 2)": 1,
            "x = region.x + (rect[0] + rect[2] / 2) * s": 1,
            "_, region, r3d = view()\npoint = location_3d_to_region_2d(region, r3d, co)\np = (region.x + point.x)": 1,
            "r = region()\np = r.x + x": 1,
        }
        for source, count in cases.items():
            self.assertEqual(len(raw_mappings(source)), count, source)

    def test_the_scan_leaves_region_geometry_and_bounds(self):
        for source in ("x, y = region.x + region.width // 2, region.y + 60",
                       "block = px[region.y:region.y + region.height, region.x:region.x + region.width]",
                       "inside = region.x <= x < region.x + region.width",
                       "ok = r.x - margin <= wx <= r.x + r.width + margin",
                       "x = int(region.x + region.width * 0.95)",
                       "x = qa_hit.point(qa_hit.LAYOUT, region, rect)[0]"):
            self.assertEqual(raw_mappings(source), [], source)


REGION = SimpleNamespace(x=100, y=50, width=400, height=300)


class QaHitArithmetic(unittest.TestCase):
    def at(self, surface, rect, scale=1.0, rtl=False, **fraction):
        with mock.patch.object(qa_hit, "ui_scale", return_value=scale), \
                mock.patch.object(qa_hit, "is_rtl", return_value=rtl):
            return qa_hit.point(surface, REGION, rect, **fraction)

    def test_layout_is_scaled_and_mirrored_about_the_region(self):
        rect = (10, 20, 40, 10)
        self.assertEqual(self.at(qa_hit.LAYOUT, rect), (130, 75))
        self.assertEqual(self.at(qa_hit.LAYOUT, rect, scale=1.5), (145, 87.5))
        self.assertEqual(self.at(qa_hit.LAYOUT, rect, scale=2.0, rtl=True), (100 + 400 - 60, 50 + 50))

    def test_composer_text_is_scaled_never_mirrored(self):
        self.assertEqual(self.at(qa_hit.COMPOSER_TEXT, (10, 20, 40, 10), scale=2.0, rtl=True), (160, 100))

    def test_pixels_are_mirrored_never_scaled(self):
        self.assertEqual(self.at(qa_hit.PIXELS, (10, 20, 40, 10), scale=2.0), (130, 75))
        self.assertEqual(self.at(qa_hit.PIXELS, (10, 20, 40, 10), scale=2.0, rtl=True), (470, 75))

    def test_raw_and_an_axis(self):
        self.assertEqual(self.at(qa_hit.RAW, (10, 20, 40, 10), scale=2.0, rtl=True), (130, 75))
        self.assertEqual(self.at(qa_hit.mirrored_about(300), (10, 20, 40, 10), rtl=False), (100 + 270, 75))
        self.assertEqual(self.at(qa_hit.mirrored_about(None), (10, 20, 40, 10), rtl=True), (130, 75))

    def test_a_mirrored_rect_keeps_its_left_bottom_corner(self):
        with mock.patch.object(qa_hit, "ui_scale", return_value=2.0), mock.patch.object(qa_hit, "is_rtl", return_value=True):
            self.assertEqual(qa_hit.rect(qa_hit.LAYOUT, REGION, (10, 20, 40, 10)), (100 + 400 - 100, 90, 80, 20))
        self.assertEqual(self.at(qa_hit.LAYOUT, (10, 20, 40, 10), fx=0, fy=1), (110, 80))

    def test_from_window_is_the_inverse(self):
        for surface in (qa_hit.LAYOUT, qa_hit.COMPOSER_TEXT, qa_hit.PIXELS, qa_hit.RAW, qa_hit.mirrored_about(250)):
            for scale in (1.0, 1.25, 2.0):
                for rtl in (False, True):
                    with mock.patch.object(qa_hit, "ui_scale", return_value=scale), \
                            mock.patch.object(qa_hit, "is_rtl", return_value=rtl):
                        rect = (12, 30, 50, 20)
                        back = qa_hit.from_window_rect(surface, REGION, qa_hit.rect(surface, REGION, rect))
                        self.assertEqual(tuple(round(v, 6) for v in back), rect, (surface, scale, rtl))

    def test_each_surface_inverts_its_own_input(self):
        """The add-on's input lines ``qa_hit`` inverts: a changed mapping fails here before a suite clicks wrong."""
        pins = {
            "chat_view.py": "canvas_rtl.pointer_x(event.mouse_x - region.x, region.width) / scale, "
                            "(event.mouse_y - region.y) / scale",
            "chat_view.py ": "plain = canvas_rtl.pointer_x(x, region.width / scale)  # The composer's text field is "
                             "never mirrored.",
            "flow_menu.py": "canvas_rtl.pointer_x(event.mouse_x - region.x, region.width) / scale",
            "card_host.py": "canvas_rtl.pointer_x(event.mouse_x - region.x, region.width), event.mouse_y - region.y",
            "intro.py": "canvas_rtl.pointer_x(event.mouse_x - region.x, region.width)",
            "connect_hero.py": "canvas_rtl.pointer_x(event.mouse_x - region.x, region.width)",
            "providers_page_host.py": "canvas_rtl.pointer_x(event.mouse_x - region.x, region.width), "
                                      "event.mouse_y - region.y",
            "flow_window.py": "canvas_rtl.pointer_x(event.mouse_x - region.x, region.width), event.mouse_y - region.y",
            "studio_input.py": 'canvas_rtl.layout_x(event.mouse_x - region.x, state.get("mirror")), '
                               "event.mouse_y - region.y",
        }
        for name, line in pins.items():
            self.assertIn(line, (ADDON / name.strip()).read_text(encoding="utf-8"), name)
        rtl = (ADDON / "canvas_rtl.py").read_text(encoding="utf-8")
        self.assertIn("return width - x if ui_text.is_rtl() else x", rtl)
        self.assertIn("return x if mirrored_about is None else mirrored_about - x", rtl)


if __name__ == "__main__":
    unittest.main()
