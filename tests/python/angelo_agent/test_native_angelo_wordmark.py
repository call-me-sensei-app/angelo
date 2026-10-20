# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Angelo rename kept the raven mark byte-for-byte and added the approved wordmark.

Dependency-free: reads only SVG/JSON sources (no Git LFS content), so it also
runs in the host-test CI job.
"""

import hashlib
import json
import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BRAND = ROOT / "release/branding/angelo"
# SHA-256 of the raven mark files' content with the copyright holder's name
# blanked (artwork(), below): the artwork, licence line and description are
# pinned byte-for-byte, while a change of copyright holder (as in 1.0.1, to
# Hyperbond Studio Pte. Ltd.) is a legal notice, not a change to the mark.
MARK_SHA256 = {
    "angelo-mark-white.svg": "785fcac4e5422bdb4954ace0ce994d9c819be1b70fd917bdbbc240bb48c08854",
    "angelo-mark-color.svg": "97de012b1b183842009b37a43cb68209c86a33a55d9729b365d6a4a0d728d790",
    "angelo-mark-dark.svg": "3a6e3b75b144e3d0f5e9bacd0336027131c2c065dd9e2c77d4266894ea20206a",
}
MASTER_SHA256 = "98aabf0e191e588fd3efeb9ddcb36c7b389d0ec4b29935d23ecbefe130c5c323"
FACET_COLORS = ("#a1f4d6", "#27bdbf", "#1aa3ad", "#0b8a99", "#087787")
# Triangle (100 x 88) minus the gap between the legs and the counter, in the A's own units.
A_AREA = 100 * 88 / 2 - (66 + 38.8) / 2 * 24 - 25 * 22 / 2


def artwork(path):
    """SHA-256 of ``path`` with every ``SPDX-FileCopyrightText:`` value blanked."""
    blanked = re.sub(rb"SPDX-FileCopyrightText:[^\n]*", b"SPDX-FileCopyrightText:", path.read_bytes())
    return hashlib.sha256(blanked).hexdigest()


def polygons(path_data):
    """Straight-edged ``M x y L x y ... Z`` subpaths as point lists."""
    result = []
    for subpath in re.findall(r"M([^MZ]*)Z", path_data):
        numbers = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", subpath)]
        result.append(list(zip(numbers[0::2], numbers[1::2])))
    return result


def area(points):
    return abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1]))) / 2


def letter_a_paths(svg_path):
    """The A group's paths: the first group scaled into the 100 x 88 letter box."""
    tree = ET.parse(svg_path)
    for group in tree.iter("{http://www.w3.org/2000/svg}g"):
        if re.search(r"scale\(3\.8\)", group.get("transform", "")):
            return list(group.iter("{http://www.w3.org/2000/svg}path"))
    raise AssertionError("No faceted A group in " + svg_path.name)


class AngeloWordmarkTests(unittest.TestCase):
    def test_raven_mark_is_unchanged_by_the_rename(self):
        for name, checksum in MARK_SHA256.items():
            with self.subTest(name=name):
                self.assertEqual(artwork(BRAND / name), checksum)
        manifest = json.loads((BRAND / "exports/manifest.json").read_text())
        self.assertEqual(manifest["master"], "angelo-raven-teal-v2.png")
        self.assertEqual(manifest["master_sha256"], MASTER_SHA256)
        self.assertFalse((ROOT / "release/branding/sensei").exists(), "Every consumer uses the Angelo folder")

    def test_wordmarks_are_font_free_paths_that_nanosvg_can_draw(self):
        for name in ("angelo-wordmark.svg", "angelo-wordmark-white.svg"):
            with self.subTest(name=name):
                text = (BRAND / name).read_text()
                tree = ET.fromstring(text)
                self.assertTrue(tree.findall(".//{*}path"))
                for unsupported in ("text", "clipPath", "mask", "use"):
                    self.assertEqual(tree.findall(".//{*}" + unsupported), [], unsupported)
                self.assertNotIn("clip-path", text)
        about = (ROOT / "release/datafiles/icons_svg/blender_logo_large.svg").read_bytes()
        self.assertEqual(about, (BRAND / "angelo-wordmark-white.svg").read_bytes())
        self.assertFalse(ET.fromstring(about).findall(".//{*}image"))

    def test_splash_source_keeps_the_lockup_on_a_reproducible_lit_background(self):
        # The splash is rendered to a PNG by librsvg, never by NanoSVG, so gradients and filters
        # are allowed; fonts, outside references and unseeded noise are not.
        text = (BRAND / "angelo-splash.svg").read_text()
        tree = ET.fromstring(text)
        self.assertEqual((tree.get("width"), tree.get("height"), tree.get("viewBox")), ("1000", "500", "0 0 1000 500"))
        for unsupported in ("text", "tspan", "foreignObject", "style", "script"):
            self.assertEqual(tree.findall(".//{*}" + unsupported), [], unsupported)
        images = tree.findall(".//{*}image")
        self.assertEqual(len(images), 1, "The approved raven bitmap, once")
        raven = images[0]
        self.assertEqual([raven.get(key) for key in ("x", "y", "width", "height")], ["50", "95", "300", "300"])
        self.assertTrue(raven.get("{http://www.w3.org/1999/xlink}href").startswith("data:image/png;base64,"))
        self.assertNotRegex(text, r'(?:href|url)\s*[=(]\s*["\']?(?:https?:|file:|//)', "No reference leaves the file")
        self.assertIn('<g transform="translate(415 175) scale(.34)"><g transform="translate(0 45.6) scale(3.8)">', text,
                      "The wordmark keeps its previous place and size")
        gradients = tree.findall(".//{*}linearGradient") + tree.findall(".//{*}radialGradient")
        self.assertGreaterEqual(len(gradients), 4, "A lit background, not a flat fill")
        colours = {stop.get("stop-color") for gradient in gradients for stop in gradient}
        self.assertTrue({"#27bdbf", "#1aa3ad", "#0b8a99", "#087787", "#a1f4d6"} <= colours, "Lit with the A's own teals")
        noise = tree.findall(".//{*}feTurbulence")
        self.assertEqual(len(noise), 1)
        self.assertTrue(noise[0].get("seed"), "A fixed seed keeps the export deterministic")
        # The grain filters the background group only; the raven and the letters are drawn after it.
        grained = [group for group in tree.findall("{*}g") if group.get("filter") == "url(#grain)"]
        self.assertEqual(len(grained), 1)
        self.assertEqual(grained[0].findall(".//{*}image"), [])
        children = list(tree)
        self.assertLess(children.index(grained[0]), children.index(raven))

    def test_colour_wordmark_and_splash_use_the_faceted_teal_a(self):
        for name in ("angelo-wordmark.svg", "angelo-splash.svg"):
            with self.subTest(name=name):
                fills = [path.get("fill") for path in letter_a_paths(BRAND / name)]
                self.assertEqual(fills[0], "#1aa3ad", "A base layer hides seams between facets")
                self.assertEqual(fills[1:], list(FACET_COLORS))

    def test_white_wordmark_keeps_the_facets_as_a_single_colour(self):
        paths = letter_a_paths(BRAND / "angelo-wordmark-white.svg")
        self.assertEqual({path.get("fill") for path in paths}, {"#ffffff"})
        opacities = [float(path.get("fill-opacity")) for path in paths[1:]]
        self.assertEqual(opacities[0], 1.0, "The apex is the lightest facet")
        self.assertTrue(all(0 < value <= 1 for value in opacities))

    def test_facets_exactly_tile_the_letter(self):
        paths = letter_a_paths(BRAND / "angelo-wordmark.svg")
        base, facets = paths[0], paths[1:]
        self.assertEqual(base.get("fill-rule"), "evenodd")
        outline = polygons(base.get("d"))
        self.assertAlmostEqual(area(outline[0]) - area(outline[1]), A_AREA, places=3)
        pieces = [piece for facet in facets for piece in polygons(facet.get("d"))]
        # Non-overlapping facets covering the A sum to its area; an overlap or gap would not.
        self.assertAlmostEqual(sum(area(piece) for piece in pieces), A_AREA, places=1)
        for piece in pieces:
            for x, y in piece:
                self.assertTrue(-1e-6 <= x <= 100 + 1e-6 and -1e-6 <= y <= 88 + 1e-6, (x, y))

    def test_letters_are_the_approved_weight_500_outlines_of_the_bundled_font(self):
        letters = (BRAND / "angelo-wordmark-letters.svg").read_text()
        font = hashlib.sha256((ROOT / "release/datafiles/fonts/Inter.woff2").read_bytes()).hexdigest()
        self.assertIn(f'data-font-sha256="{font}"', letters, "Rerun build_files/utils/angelo_letters.mjs")
        self.assertIn("at weight 500, optical size 32, letter spacing -0.015 em, 440 px", letters)
        self.assertIn("wght1F40000", letters, "Chromium used the weight-500 variable instance")
        paths = ET.fromstring(letters).findall("{*}path")
        pens = [float(re.match(r"translate\(([\d.]+) 380\)", path.get("transform")).group(1)) for path in paths]
        self.assertEqual(len(paths), 5)
        self.assertEqual(pens[0], 0)
        self.assertTrue(all(b > a for a, b in zip(pens, pens[1:])))
        # Deliberate substitution: the "l" is a plain stem, one rectangle flush with the
        # baseline, with equal side bearings; the -0.015 em tracking follows it.
        rectangle = polygons(paths[3].get("d"))
        self.assertEqual(len(rectangle), 1)
        xs, ys = sorted({x for x, _ in rectangle[0]}), sorted({y for _, y in rectangle[0]})
        self.assertEqual((len(rectangle[0]), len(xs), len(ys)), (4, 2, 2))
        self.assertEqual(ys[1], 0, "flush with the baseline")
        self.assertAlmostEqual(ys[0], -320.117, places=3)
        self.assertAlmostEqual(pens[4] - pens[3], xs[0] + (xs[1] - xs[0]) + xs[0] - 0.015 * 440, places=3)
        # Every exported wordmark carries these outlines unchanged, 0.02 em after the A.
        expected = letters[letters.index("<path"):letters.rindex("</svg>")]
        for name in ("angelo-wordmark.svg", "angelo-wordmark-white.svg", "angelo-splash.svg"):
            with self.subTest(name=name):
                self.assertIn('<g transform="translate(388.8 0)">' + expected + "</g>", (BRAND / name).read_text())

    def test_selection_records_the_approved_wordmark(self):
        selection = json.loads((BRAND / "selection.json").read_text())
        self.assertEqual(selection["app_name"], "Angelo")
        self.assertEqual(selection["previous_app_name"], "Sensei")
        self.assertEqual(selection["wordmark"], "angelo-wordmark.svg")
        self.assertIn("Option B", selection["wordmark_selection"])
        coverage = json.loads((BRAND / "coverage.json").read_text())
        self.assertEqual((coverage["wordmark"], coverage["wordmark_white"]),
                         ("angelo-wordmark.svg", "angelo-wordmark-white.svg"))


if __name__ == "__main__":
    unittest.main()
